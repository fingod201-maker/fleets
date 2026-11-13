"""Health strip probes. Every probe degrades to a status string; nothing raises."""

from __future__ import annotations

import shutil
import socket
import ssl
import time
from dataclasses import asdict, dataclass

import httpx

from .config import Settings


@dataclass
class Probe:
    name: str
    status: str
    detail: str = ""


def _http_ok(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        resp = httpx.get(url, timeout=timeout)
        return resp.status_code < 400, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, type(exc).__name__


def _tcp_ok(host: str, port: int, timeout: float = 1.5) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} open"
    except OSError as exc:
        return False, type(exc).__name__


def _tls_expiry(path: str) -> tuple[bool, str]:
    try:
        decoded = ssl._ssl._test_decode_cert(path)  # type: ignore[attr-defined]
    except (OSError, ValueError) as exc:
        return False, type(exc).__name__
    not_after = decoded.get("notAfter", "")
    try:
        expires = ssl.cert_time_to_seconds(not_after)
    except ValueError:
        return False, not_after
    days = (expires - time.time()) / 86400
    return days > 7, f"{days:.0f}d left"


def snapshot(config: Settings) -> dict[str, Probe]:
    probes: dict[str, Probe] = {}

    ok, detail = _http_ok(f"{config.bridge_url}/healthz")
    probes["bridge"] = Probe("bridge", "up" if ok else "down", detail)

    ok, detail = _http_ok(f"{config.bridge_url}/sliver/version")
    probes["teamserver"] = Probe("teamserver", "up" if ok else "down", detail)

    ok, detail = _tcp_ok(config.rd_bind, config.rd_port)
    probes["rd_broker"] = Probe("rd_broker", "up" if ok else "down", detail)

    try:
        usage = shutil.disk_usage(str(config.db_path.parent))
        free_gb = usage.free / (1024**3)
        probes["disk"] = Probe("disk", "ok" if free_gb > 2 else "low", f"{free_gb:.1f}G free")
    except OSError as exc:
        probes["disk"] = Probe("disk", "down", type(exc).__name__)

    if config.tls_cert:
        ok, detail = _tls_expiry(config.tls_cert)
        probes["tls"] = Probe("tls", "ok" if ok else "warn", detail)
    else:
        probes["tls"] = Probe("tls", "n/a", "no cert configured")

    return probes


def as_dict(config: Settings) -> dict[str, dict[str, str]]:
    return {name: asdict(probe) for name, probe in snapshot(config).items()}
