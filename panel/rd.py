"""Remote-desktop broker.

Helpers (rd-win/rd-mac/rd-linux) connect out to this broker on 8443 with a
per-session HMAC token; operator browsers connect on the panel as well. The
broker relays JPEG frames helper->browsers and input events browsers->helper.
No inbound connections to the target are ever required.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import time
from collections import defaultdict
from contextlib import suppress
from urllib.parse import parse_qs, urlparse

from .config import Settings

log = logging.getLogger("fleet.rd")


def mint_token(secret: str, session_id: str, ttl: int = 3600) -> str:
    expires = int(time.time()) + ttl
    message = f"{session_id}:{expires}".encode()
    mac = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:32]
    return f"{expires}.{mac}"


def verify_token(secret: str, session_id: str, token: str) -> bool:
    if not token or "." not in token:
        return False
    expires_str, mac = token.split(".", 1)
    try:
        expires = int(expires_str)
    except ValueError:
        return False
    if expires < time.time():
        return False
    expected = hmac.new(
        secret.encode(), f"{session_id}:{expires}".encode(), hashlib.sha256
    ).hexdigest()[:32]
    return hmac.compare_digest(expected, mac)


class RDBroker:
    def __init__(self, config: Settings, on_creds=None) -> None:
        self.config = config
        self.on_creds = on_creds
        self._helpers: dict[str, object] = {}
        self._browsers: dict[str, set[asyncio.Queue[bytes | str]]] = defaultdict(set)
        self._last_hello: dict[str, str] = {}
        self._last_frame: dict[str, bytes] = {}
        self._server: object | None = None

    def forget(self, session_id: str) -> None:
        """Drop stale hello metadata before launching a new helper."""
        self._last_hello.pop(session_id, None)

    def last_frame(self, session_id: str) -> bytes | None:
        return self._last_frame.get(session_id)

    def mint(self, session_id: str, ttl: int = 3600) -> str:
        return mint_token(self.config.panel_secret, session_id, ttl)

    def helper_connected(self, session_id: str) -> bool:
        return session_id in self._helpers

    async def start(self) -> None:
        try:
            from websockets.asyncio.server import serve
        except ImportError:  # pragma: no cover - dependency present in requirements
            log.warning("websockets not available; rd broker disabled")
            return
        self._server = await serve(
            self._helper_handler, self.config.rd_bind, self.config.rd_port, ping_interval=20
        )
        log.info("rd broker listening on %s:%d", self.config.rd_bind, self.config.rd_port)

    async def stop(self) -> None:
        """Close helpers/browsers fast so systemd restarts never hang (was 90s)."""
        if self._server is None:
            return
        self._server.close()
        for connection in list(self._helpers.values()):
            with suppress(Exception):
                await connection.close(code=1001, reason="shutdown")  # type: ignore[attr-defined]
        self._helpers.clear()
        self._browsers.clear()
        with suppress(Exception):
            await asyncio.wait_for(self._server.wait_closed(), timeout=3)  # type: ignore[attr-defined]

    # -- helper side ---------------------------------------------------------
    async def _helper_handler(self, connection: object) -> None:
        request = connection.request  # type: ignore[attr-defined]
        parsed = urlparse(request.path)
        session_id = parsed.path.rsplit("/", 1)[-1]
        token = parse_qs(parsed.query).get("token", [""])[0]
        ok = verify_token(self.config.panel_secret, session_id, token)
        log.warning("rd helper handshake session=%s verified=%s", session_id[:8], ok)
        if not ok:
            await connection.close(  # type: ignore[attr-defined]
                code=4001, reason="bad rd token"
            )
            return
        # one helper per session: the newest connection replaces the old one
        previous = self._helpers.get(session_id)
        if previous is not None and previous is not connection:
            with suppress(Exception):
                await previous.close(code=4000, reason="replaced")  # type: ignore[attr-defined]
        self._helpers[session_id] = connection
        log.info("rd helper connected for %s", session_id)
        try:
            async for message in connection:  # type: ignore[attr-defined]
                if self._helpers.get(session_id) is not connection:
                    break
                if isinstance(message, str):
                    if '"t":"hello"' in message.replace(" ", ""):
                        self._last_hello[session_id] = message
                    await self._maybe_creds(session_id, message)
                else:
                    self._last_frame[session_id] = message
                await self._fanout(session_id, message)
        finally:
            if self._helpers.get(session_id) is connection:
                del self._helpers[session_id]
                self._last_hello.pop(session_id, None)
            self._browsers.pop(session_id, None)
            log.info("rd helper disconnected for %s", session_id)

    # -- browser side --------------------------------------------------------
    def subscribe(self, session_id: str) -> asyncio.Queue[bytes | str]:
        queue: asyncio.Queue[bytes | str] = asyncio.Queue(maxsize=8)
        # replay the helper hello so late subscribers still learn mode/size/caps
        hello = self._last_hello.get(session_id)
        if hello:
            with suppress(asyncio.QueueFull):
                queue.put_nowait(hello)
        self._browsers[session_id].add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[bytes | str]) -> None:
        self._browsers.get(session_id, set()).discard(queue)

    async def send_input(self, session_id: str, payload: str) -> None:
        helper = self._helpers.get(session_id)
        if helper is None:
            return
        try:
            await helper.send(payload)  # type: ignore[attr-defined]
        except Exception as exc:  # surfaced in logs, never raised into the request
            log.warning("rd input to %s failed: %s", session_id, exc)

    async def _maybe_creds(self, session_id: str, message: str) -> None:
        if self.on_creds is None or '"t":"creds"' not in message.replace(" ", ""):
            return
        import json

        try:
            data = json.loads(message)
        except ValueError:
            return
        if data.get("t") != "creds":
            return
        try:
            result = self.on_creds(session_id, data)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:  # never break the relay
            log.warning("creds hook failed for %s: %s", session_id, exc)

    async def _fanout(self, session_id: str, message: bytes | str) -> None:
        for queue in list(self._browsers.get(session_id, set())):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                except asyncio.QueueEmpty:
                    log.debug("rd queue race for %s", session_id)
