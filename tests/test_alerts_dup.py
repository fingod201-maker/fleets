import asyncio
import json

from panel.alerts import Alerts, fingerprint
from panel.db import Database


class FakeTG:
    def __init__(self):
        self.sent = []

    def send(self, html, caption=""):
        self.sent.append(html)


class FakeBridge:
    def info(self, session_id):
        return {"interfaces": []}

    def ps(self, session_id):
        return {"processes": []}

    def health(self):
        return {"connected": True}

    def sessions(self):
        return []


def make(tmp):
    db = Database(tmp.db_path)
    db.migrate()
    tg = FakeTG()
    return Alerts(tmp, tg, FakeBridge(), db), tg, db


def ev(session_id="s1", **over):
    session = {
        "id": session_id, "host": "h", "user": "u", "os": "windows", "arch": "amd64",
        "integrity": "High", "transport": "mtls", "remote_address": "127.0.0.1:5",
        "reconnect_interval": 60, "active_c2": "mtls://127.0.0.1:8888", "pid": 1,
    }
    session.update(over)
    return json.dumps({"type": "session-connected", "session": session})


def test_fingerprint_changes_with_core_data():
    a = {"id": "x", "host": "h", "user": "u"}
    b = {"id": "x", "host": "h", "user": "v"}
    assert fingerprint(a) != fingerprint(b)


def test_dedup_only_once_per_content(tmp_settings):
    alerts, tg, _ = make(tmp_settings)
    asyncio.run(alerts._handle(ev()))
    assert len(tg.sent) == 1
    asyncio.run(alerts._handle(ev()))
    assert len(tg.sent) == 1  # identical re-emission suppressed
    asyncio.run(alerts._handle(ev(user="u2")))
    assert len(tg.sent) == 2  # genuine change -> one alert
    assert "CHANGED" in tg.sent[-1]


def test_dedup_persists_across_restart(tmp_settings):
    alerts, tg, _ = make(tmp_settings)
    asyncio.run(alerts._handle(ev()))
    assert len(tg.sent) == 1
    # new process (fresh Alerts) sharing the same DB must not re-alert
    alerts2, tg2, _ = make(tmp_settings)
    asyncio.run(alerts2._handle(ev()))
    assert len(tg2.sent) == 0


def test_panel_start_throttled(tmp_settings):
    alerts, tg, _ = make(tmp_settings)
    alerts.panel_start()
    alerts.panel_start()
    assert len(tg.sent) == 1


def test_health_status_posts(tmp_settings):
    alerts, tg, _ = make(tmp_settings)
    asyncio.run(alerts._emit_health())
    assert any("health" in m for m in tg.sent)
    assert any("sessions" in m for m in tg.sent)
