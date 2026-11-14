import asyncio
import dataclasses

from panel.db import Database
from panel.rd import RDBroker


def test_add_and_list_creds(tmp_settings):
    db = Database(tmp_settings.db_path)
    db.migrate()
    db.add_cred("sess-1", "alice", "hunter2", "prompt")
    db.add_cred("sess-1", "bob", "s3cret", "hvnc")
    db.add_cred("sess-2", "carol", "pw", "prompt")
    rows = db.creds_for("sess-1")
    assert len(rows) == 2
    assert {r["username"] for r in rows} == {"alice", "bob"}
    assert db.creds_for("sess-2")[0]["secret"] == "pw"


def test_creds_are_not_in_audit_by_default(tmp_settings):
    db = Database(tmp_settings.db_path)
    db.migrate()
    db.add_cred("s", "u", "p", "prompt")
    assert all((r["detail"] or "") != "p" for r in db.recent_audit())


def test_broker_on_creds_hook(tmp_settings):
    captured = []

    def hook(session_id, data):
        captured.append((session_id, data.get("user"), data.get("pass")))

    broker = RDBroker(tmp_settings, on_creds=hook)
    asyncio.run(broker._maybe_creds("sid", '{"t":"creds","user":"a","pass":"b"}'))
    assert captured == [("sid", "a", "b")]


def test_broker_hook_ignores_non_creds(tmp_settings):
    captured = []
    broker = RDBroker(tmp_settings, on_creds=lambda *a: captured.append(a))
    asyncio.run(broker._maybe_creds("sid", '{"t":"hello"}'))
    assert captured == []


def test_broker_hook_tolerates_spaces(tmp_settings):
    captured = []
    broker = RDBroker(tmp_settings, on_creds=lambda sid, d: captured.append(d.get("pass")))
    asyncio.run(broker._maybe_creds("sid", '{ "t" : "creds" , "user":"x", "pass":"z" }'))
    assert captured == ["z"]


def test_settings_has_bin_dir(tmp_settings):
    settings = dataclasses.replace(tmp_settings)
    assert settings.panel_bin_dir
    assert settings.rd_endpoint
