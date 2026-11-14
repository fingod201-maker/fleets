import asyncio

from panel.db import Database
from panel.sliver import BridgeError
from panel.views import BridgeViews, human_age


class FakeBridge:
    def __init__(self, sessions=None, fail=False):
        self._sessions = sessions or []
        self._fail = fail

    def sessions(self):
        if self._fail:
            raise BridgeError("down")
        return self._sessions

    def ps(self, session_id):
        return {"processes": [{"pid": 1}]}

    def ls(self, session_id, path):
        return {"path": path, "files": []}

    def info(self, session_id):
        return {"os": "linux"}


def views(tmp_settings, bridge):
    db = Database(tmp_settings.db_path)
    db.migrate()
    return BridgeViews(tmp_settings, db, bridge)


def run(coro):
    return asyncio.run(coro)


def test_human_age():
    assert human_age(5) == "5s"
    assert human_age(120) == "2m"
    assert human_age(7200) == "2h"
    assert human_age(172800) == "2d"


def test_implants_merges_tags(tmp_settings):
    v = views(
        tmp_settings, FakeBridge([{"id": "abc", "host": "h1", "os": "linux", "last_checkin": 0}])
    )
    v.set_tags("abc", ["prod", "web", "prod"])
    sessions, err = run(v.implants())
    assert err == ""
    assert sessions[0]["tags"] == ["prod", "web"]


def test_implants_reports_bridge_error(tmp_settings):
    v = views(tmp_settings, FakeBridge(fail=True))
    sessions, err = run(v.implants())
    assert sessions == []
    assert "down" in err


def test_implants_sorted_and_age(tmp_settings):
    v = views(
        tmp_settings,
        FakeBridge(
            [
                {"id": "b", "host": "zeta", "os": "linux", "last_checkin": 0},
                {"id": "a", "host": "alpha", "os": "linux", "last_checkin": 0},
            ]
        ),
    )
    sessions, _ = run(v.implants())
    assert [s["host"] for s in sessions] == ["alpha", "zeta"]
    assert sessions[0]["age"] == "-"


def test_procs_and_info(tmp_settings):
    v = views(tmp_settings, FakeBridge())
    assert run(v.procs("x")) == [{"pid": 1}]
    assert run(v.info("x")) == {"os": "linux"}
