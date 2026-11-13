"""Data shaping between the bridge/DB and the templates.

The bridge already returns normalized JSON; this layer merges panel-side state
(tags, recorded public IP) and derives display values (age strings).
"""

from __future__ import annotations

import time
from typing import Any

from starlette.concurrency import run_in_threadpool

from .config import Settings
from .db import Database
from .sliver import BridgeError, SliverBridge


def human_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


class BridgeViews:
    def __init__(self, config: Settings, database: Database, bridge: SliverBridge) -> None:
        self.config = config
        self.db = database
        self.bridge = bridge

    # -- tags ----------------------------------------------------------------
    def tags_for(self, session_id: str) -> list[str]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT tags FROM tags WHERE session_id = ?", (session_id,)
            ).fetchone()
        if not row or not row["tags"]:
            return []
        return [tag for tag in str(row["tags"]).split(",") if tag]

    def set_tags(self, session_id: str, tags: list[str]) -> None:
        clean = ",".join(sorted({tag.strip() for tag in tags if tag.strip()}))
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO tags (session_id, tags, updated) VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "tags = excluded.tags, updated = excluded.updated",
                (session_id, clean, time.time()),
            )

    def _ip_for(self, session_id: str) -> tuple[str, str]:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT public_ip, local_ips FROM implant_ip WHERE session_id = ?", (session_id,)
            ).fetchone()
        if not row:
            return "", ""
        return str(row["public_ip"]), str(row["local_ips"])

    def set_ip(self, session_id: str, public_ip: str, local_ips: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO implant_ip (session_id, public_ip, local_ips, updated) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                "public_ip = excluded.public_ip, local_ips = excluded.local_ips, "
                "updated = excluded.updated",
                (session_id, public_ip, local_ips, time.time()),
            )

    # -- sessions ------------------------------------------------------------
    async def implants(self) -> tuple[list[dict[str, Any]], str]:
        try:
            raw = await run_in_threadpool(self.bridge.sessions)
        except BridgeError as exc:
            return [], str(exc)
        now = time.time()
        sessions: list[dict[str, Any]] = []
        for item in raw:
            session_id = str(item.get("id", ""))
            public_ip, local_ips = self._ip_for(session_id)
            last = float(item.get("last_checkin", 0) or 0)
            sessions.append(
                {
                    **item,
                    "tags": self.tags_for(session_id),
                    "public_ip": public_ip,
                    "local_ips": local_ips,
                    "age": human_age(now - last) if last else "-",
                    "alive": (now - last) < 300 if last else False,
                }
            )
        sessions.sort(key=lambda s: s.get("host", ""))
        return sessions, ""

    async def info(self, session_id: str) -> dict[str, Any]:
        try:
            data = await run_in_threadpool(self.bridge.info, session_id)
        except BridgeError as exc:
            return {"error": str(exc)}
        return data

    async def procs(self, session_id: str) -> list[dict[str, Any]]:
        try:
            data = await run_in_threadpool(self.bridge.ps, session_id)
        except BridgeError:
            return []
        return list(data.get("processes", []))

    async def files(self, session_id: str, path: str) -> dict[str, Any]:
        try:
            return await run_in_threadpool(self.bridge.ls, session_id, path)
        except BridgeError as exc:
            return {"error": str(exc), "files": []}
