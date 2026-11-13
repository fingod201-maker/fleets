"""Authentication: bcrypt passwords, server-side sessions, CSRF, TOTP, lockout.

Sessions are server-side (the cookie only carries a signed opaque id), so the
invalidation list in SPEC §5.1 is real and revocable. The cookie is signed with
itsdangerous and carries an expiry; the DB row carries validity.
"""

from __future__ import annotations

import hmac
import secrets
import sqlite3
import time

import bcrypt
import pyotp
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import Settings
from .db import Database


class Auth:
    def __init__(self, config: Settings, database: Database) -> None:
        self.config = config
        self.db = database
        self.serializer = URLSafeTimedSerializer(config.panel_secret, salt="fleet-session")

    # -- password ------------------------------------------------------------
    def verify_password(self, password: str) -> bool:
        stored = self.config.panel_pass_hash
        if not stored:
            return False
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored.encode("utf-8"))
        except ValueError:
            return False

    def verify_totp(self, code: str) -> bool:
        secret = self.config.panel_totp_secret
        if not secret:
            return True
        if not code:
            return False
        return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)

    # -- lockout -------------------------------------------------------------
    def lockout_remaining(self, key: str) -> float:
        with self.db.connect() as conn:
            row = conn.execute("SELECT locked_until FROM lockout WHERE key = ?", (key,)).fetchone()
        if not row:
            return 0.0
        return max(0.0, float(row["locked_until"]) - time.time())

    def register_failure(self, key: str) -> None:
        now = time.time()
        with self.db.connect() as conn:
            row = conn.execute("SELECT fails FROM lockout WHERE key = ?", (key,)).fetchone()
            fails = (int(row["fails"]) if row else 0) + 1
            locked_until = 0.0
            if fails >= self.config.lockout_max:
                locked_until = now + self.config.lockout_seconds
                fails = 0
            conn.execute(
                "INSERT INTO lockout (key, fails, last_fail, locked_until) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET fails = excluded.fails, "
                "last_fail = excluded.last_fail, locked_until = excluded.locked_until",
                (key, fails, now, locked_until),
            )

    def clear_failures(self, key: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM lockout WHERE key = ?", (key,))

    def recent_failures(self, window_seconds: int = 300) -> int:
        since = time.time() - window_seconds
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM audit "
                "WHERE action = 'login' AND result = 'fail' AND ts >= ?",
                (since,),
            ).fetchone()
        return int(row["n"]) if row else 0

    # -- sessions ------------------------------------------------------------
    def create_session(self, actor: str, ip: str, ua: str) -> tuple[str, str]:
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        now = time.time()
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, actor, created, last_seen, ip, ua, csrf, valid) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                (session_id, actor, now, now, ip, ua, csrf),
            )
        return session_id, csrf

    def get_session(self, session_id: str) -> sqlite3.Row | None:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ? AND valid = 1", (session_id,)
            ).fetchone()
        if not row:
            return None
        if time.time() - float(row["last_seen"]) > self.config.session_ttl:
            self.invalidate(session_id)
            return None
        return row

    def touch_session(self, session_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE sessions SET last_seen = ? WHERE id = ?", (time.time(), session_id)
            )

    def invalidate(self, session_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("UPDATE sessions SET valid = 0 WHERE id = ?", (session_id,))

    def invalidate_all(self) -> int:
        with self.db.connect() as conn:
            cur = conn.execute("UPDATE sessions SET valid = 0 WHERE valid = 1")
            return cur.rowcount

    # -- cookie --------------------------------------------------------------
    def sign(self, session_id: str) -> str:
        return self.serializer.dumps(session_id)

    def unsign(self, cookie: str) -> str | None:
        try:
            return self.serializer.loads(cookie, max_age=self.config.session_ttl)
        except (BadSignature, SignatureExpired):
            return None

    @staticmethod
    def verify_csrf(expected: str, provided: str) -> bool:
        if not expected or not provided:
            return False
        return hmac.compare_digest(expected, provided)
