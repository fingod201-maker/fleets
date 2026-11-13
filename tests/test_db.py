import sqlite3

import pytest
from panel.db import Database


def test_migrate_is_idempotent(tmp_settings):
    db = Database(tmp_settings.db_path)
    assert db.migrate() == 2
    assert db.migrate() == 2


def test_audit_rows_appear(tmp_settings):
    db = Database(tmp_settings.db_path)
    db.migrate()
    db.audit("operator", "login", "ok", target="127.0.0.1", detail="x")
    rows = db.recent_audit()
    assert len(rows) == 1
    assert rows[0]["action"] == "login"
    assert rows[0]["result"] == "ok"


def test_audit_is_append_only(tmp_settings):
    db = Database(tmp_settings.db_path)
    db.migrate()
    db.audit("operator", "login", "ok")
    with db.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE audit SET action = 'changed' WHERE id = 1")
        conn.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM audit WHERE id = 1")
        conn.rollback()
    assert db.recent_audit()[0]["action"] == "login"


def test_kv_roundtrip(tmp_settings):
    db = Database(tmp_settings.db_path)
    db.migrate()
    assert db.kv_get("missing") is None
    db.kv_set("k", "v1")
    db.kv_set("k", "v2")
    assert db.kv_get("k") == "v2"
