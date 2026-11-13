"""HTTP/WS client for the Go Sliver bridge (``bridge/``).

The panel never touches Sliver's gRPC directly; the bridge owns the pinned,
grep-verified Sliver API. This client adds reconnect + exponential backoff so a
restarting teamserver/bridge never wedges the panel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings


class BridgeError(RuntimeError):
    """Raised when the bridge is unreachable or returns an error."""


@dataclass
class _Backoff:
    base: float = 0.25
    max_delay: float = 5.0
    attempts: int = 0
    next_at: float = field(default_factory=time.monotonic)

    def ready(self) -> bool:
        return time.monotonic() >= self.next_at

    def success(self) -> None:
        self.attempts = 0
        self.next_at = time.monotonic()

    def failure(self) -> float:
        self.attempts += 1
        delay = min(self.max_delay, self.base * (2 ** min(self.attempts, 6)))
        self.next_at = time.monotonic() + delay
        return delay


class SliverBridge:
    def __init__(self, config: Settings) -> None:
        self.config = config
        self._client = httpx.Client(base_url=config.bridge_url, timeout=10.0)
        self._backoff = _Backoff()

    @property
    def available(self) -> bool:
        return self._backoff.ready()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            resp = self._client.request(method, path, **kwargs)
            resp.raise_for_status()
            self._backoff.success()
            return resp
        except httpx.HTTPStatusError as exc:
            raise BridgeError(
                f"{path}: HTTP {exc.response.status_code} {exc.response.text[:200]}"
            ) from exc
        except httpx.HTTPError as exc:
            delay = self._backoff.failure()
            raise BridgeError(f"{path}: {type(exc).__name__} (retry in {delay:.2f}s)") from exc

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._request(method, path, **kwargs).json()

    # -- probes --------------------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self._json("GET", "/healthz")

    def version(self) -> dict[str, Any]:
        return self._json("GET", "/sliver/version")

    # -- collections ---------------------------------------------------------
    def sessions(self) -> list[dict[str, Any]]:
        return list(self._json("GET", "/sliver/sessions").get("sessions", []))

    def beacons(self) -> list[dict[str, Any]]:
        return list(self._json("GET", "/sliver/beacons").get("beacons", []))

    def listeners(self) -> list[dict[str, Any]]:
        return list(self._json("GET", "/sliver/jobs").get("jobs", []))

    # -- per-session ---------------------------------------------------------
    def info(self, session_id: str) -> dict[str, Any]:
        return self._json("GET", f"/sliver/session/{session_id}/info")

    def exec(
        self, session_id: str, path: str, args: list[str], output: bool = True
    ) -> dict[str, Any]:
        return self._json(
            "POST",
            f"/sliver/session/{session_id}/exec",
            json={"path": path, "args": args, "output": output},
        )

    def ls(self, session_id: str, path: str) -> dict[str, Any]:
        return self._json("POST", f"/sliver/session/{session_id}/ls", json={"path": path})

    def upload(self, session_id: str, remote_path: str, name: str, data: bytes) -> dict[str, Any]:
        files = {"file": (name, data, "application/octet-stream")}
        form = {"path": remote_path}
        return self._json("POST", f"/sliver/session/{session_id}/upload", data=form, files=files)

    def download(self, session_id: str, path: str) -> bytes:
        return self._request(
            "GET", f"/sliver/session/{session_id}/download", params={"path": path}
        ).content

    def ps(self, session_id: str, full: bool = True) -> dict[str, Any]:
        return self._json("POST", f"/sliver/session/{session_id}/ps", json={"full": full})

    def kill(self, session_id: str, pid: int, force: bool = False) -> dict[str, Any]:
        return self._json(
            "POST", f"/sliver/session/{session_id}/kill", json={"pid": pid, "force": force}
        )

    def ifconfig(self, session_id: str) -> dict[str, Any]:
        return self._json("POST", f"/sliver/session/{session_id}/ifconfig")

    def getsystem(self, session_id: str, path: str, args: list[str]) -> dict[str, Any]:
        return self._json("GET", f"/sliver/session/{session_id}/getsystem")

    def rename(self, session_id: str, name: str) -> dict[str, Any]:
        return self._json("POST", f"/sliver/session/{session_id}/rename", json={"name": name})

    # -- builds --------------------------------------------------------------
    def generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._json("POST", "/sliver/generate", json=payload)

    # -- websockets ----------------------------------------------------------
    def open_ws(self, path: str) -> Any:
        url = self.config.bridge_url.replace("http://", "ws://").replace("https://", "wss://")
        return _WebSocket(url + path)

    def is_connected(self) -> bool:
        try:
            return bool(self.health().get("connected"))
        except BridgeError:
            return False


class _WebSocket:
    """Thin sync facade over ``websockets.sync.client.connect``."""

    def __init__(self, url: str) -> None:
        from websockets.sync.client import connect

        self._conn = connect(url, open_timeout=10, close_timeout=5)

    def send(self, data: bytes | str) -> None:
        self._conn.send(data)

    def recv(self, timeout: float | None = None) -> bytes | str:
        return self._conn.recv(timeout=timeout)

    def close(self) -> None:
        self._conn.close()
