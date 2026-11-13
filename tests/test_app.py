import re

from panel.app import create_app
from starlette.testclient import TestClient

CSRF_RE = re.compile(r'name="csrf" value="([^"]+)"')


def login(client: TestClient, password: str = "correct horse battery staple 00"):
    page = client.get("/login")
    csrf = CSRF_RE.search(page.text).group(1)
    return client.post(
        "/login",
        data={"username": "operator", "password": password, "totp": "", "csrf": csrf},
        follow_redirects=False,
    )


def test_healthz(tmp_settings):
    client = TestClient(create_app(tmp_settings))
    assert client.get("/healthz").text.strip() == "ok"
    assert client.get("/healthz.json").status_code == 200


def test_login_flow_writes_audit(tmp_settings):
    app = create_app(tmp_settings)
    client = TestClient(app, follow_redirects=False)
    response = login(client)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert client.get("/").status_code == 200
    rows = app.state.db.recent_audit()
    assert any(r["action"] == "login" and r["result"] == "ok" for r in rows)


def test_dashboard_requires_login(tmp_settings):
    client = TestClient(create_app(tmp_settings), follow_redirects=False)
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_bad_password_audits_and_redirects(tmp_settings):
    app = create_app(tmp_settings)
    client = TestClient(app, follow_redirects=False)
    response = login(client, "wrong")
    assert response.status_code == 303
    assert "error=invalid" in response.headers["location"]
    assert any(r["result"] == "fail" for r in app.state.db.recent_audit())


def test_lockout_after_five_failures(tmp_settings):
    app = create_app(tmp_settings)
    client = TestClient(app, follow_redirects=False)
    for _ in range(5):
        login(client, "wrong")
    response = login(client, "wrong")
    assert "error=locked" in response.headers["location"]


def test_logout_invalidates_session(tmp_settings):
    app = create_app(tmp_settings)
    client = TestClient(app, follow_redirects=False)
    login(client)
    page = client.get("/")
    csrf = CSRF_RE.search(page.text).group(1)
    client.post("/logout", data={"csrf": csrf})
    assert client.get("/").status_code == 303


def test_audit_csv_export(tmp_settings):
    app = create_app(tmp_settings)
    client = TestClient(app, follow_redirects=False)
    login(client)
    csv = client.get("/audit.csv")
    assert csv.status_code == 200
    assert csv.text.splitlines()[0] == "id,ts,actor,action,target,result,detail"
