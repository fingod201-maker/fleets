import dataclasses
import re

from panel import mesh as mesh_module
from panel.app import create_app
from starlette.testclient import TestClient

CSRF_RE = re.compile(r'"csrf" value="([^"]+)"')


def login(client: TestClient) -> None:
    page = client.get("/login")
    csrf = CSRF_RE.search(page.text).group(1)
    response = client.post(
        "/login",
        data={
            "username": "operator",
            "password": "correct horse battery staple 00",
            "totp": "",
            "csrf": csrf,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_mesh_page_requires_login(tmp_settings):
    client = TestClient(create_app(tmp_settings), follow_redirects=False)
    response = client.get("/mesh")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_mesh_status_requires_auth(tmp_settings):
    client = TestClient(create_app(tmp_settings), follow_redirects=False)
    response = client.get("/api/mesh/status")
    assert response.status_code == 401


def test_mesh_page_renders_for_operator(tmp_settings):
    client = TestClient(create_app(tmp_settings))
    login(client)
    response = client.get("/mesh")
    assert response.status_code == 200
    assert "mesh central" in response.text.lower()


def test_mesh_status_reports_disabled(tmp_settings):
    client = TestClient(create_app(tmp_settings))
    login(client)
    response = client.get("/api/mesh/status")
    assert response.status_code == 200
    body = response.json()
    assert body["meshcentral"]["enabled"] is False
    assert body["meshcentral"]["ok"] is False
    assert body["agent_server_url"] == ""


def test_mesh_devices_requires_auth(tmp_settings):
    client = TestClient(create_app(tmp_settings), follow_redirects=False)
    response = client.get("/api/mesh/devices")
    assert response.status_code == 401


def test_mesh_devices_disabled(tmp_settings):
    client = TestClient(create_app(tmp_settings))
    login(client)
    response = client.get("/api/mesh/devices")
    assert response.status_code == 200
    body = response.json()
    assert body["meshcentral"]["ok"] is False
    assert body["devices"] == []


def test_mesh_devices_no_admin_creds(tmp_settings):
    settings = dataclasses.replace(
        tmp_settings, meshcentral_enabled=True, meshcentral_admin_user="", meshcentral_admin_pass=""
    )
    result = mesh_module._list_agents(settings)
    assert result.ok is False
    assert "credentials" in result.detail.lower()


def test_mesh_devices_lists_agents(tmp_settings, monkeypatch):
    async def fake_devices(config):
        return mesh_module.MeshCentralDevices(
            ok=True,
            detail="ok",
            devices=[
                mesh_module.MeshCentralDevice(
                    id="ag1",
                    name="host-a",
                    os="windows",
                    status="online",
                    last_seen=1719000000.0,
                )
            ],
        )

    monkeypatch.setattr(mesh_module, "devices", fake_devices)
    client = TestClient(create_app(tmp_settings))
    login(client)
    response = client.get("/api/mesh/devices")
    assert response.status_code == 200
    body = response.json()
    assert body["meshcentral"]["ok"] is True
    assert len(body["devices"]) == 1
    assert body["devices"][0]["name"] == "host-a"


def test_agent_url_maps_os(tmp_settings):
    assert mesh_module.agent_url(tmp_settings, "windows") == ""
    configured = dataclasses.replace(
        tmp_settings,
        meshcentral_enabled=True,
        meshcentral_url="https://mesh.example.com/",
        meshcentral_mesh_id="a0hQ/x==",
    )
    assert mesh_module.agent_url(configured, "windows") == (
        "https://mesh.example.com/meshagents?id=4&meshid=a0hQ%2Fx%3D%3D"
    )
    assert mesh_module.agent_url(configured, "macos") == (
        "https://mesh.example.com/meshagents?meshinstall=10005&id=a0hQ%2Fx%3D%3D"
    )
    assert mesh_module.agent_url(configured, "linux") == (
        "https://mesh.example.com/meshagents?meshinstall=6&id=a0hQ%2Fx%3D%3D"
    )
    assert mesh_module.agent_url(configured, "android") == (
        "https://mesh.example.com/meshagents?id=14"
    )
    assert mesh_module.agent_url(configured, "all") == ""


def test_agent_matrix_preconfigured(tmp_settings):
    assert mesh_module.agent_matrix(tmp_settings) == []
    configured = dataclasses.replace(
        tmp_settings,
        meshcentral_enabled=True,
        meshcentral_url="https://mesh.example.com/",
        meshcentral_mesh_id="m1",
    )
    rows = mesh_module.agent_matrix(configured)
    urls = {row["os"]: row["url"] for row in rows}
    assert urls["Windows x86-64 (service)"].endswith("/meshagents?id=4&meshid=m1")
    assert urls["macOS installer script (universal binary)"] == (
        "/__lure/meshagent?os=macos"
    )
    assert urls["Linux / BSD installer script (auto-arch)"] == (
        "/__lure/meshagent?os=linux"
    )
    assert urls["Android (APK + invite/QR)"].endswith("/meshagents?id=14")


def test_invite_url_prefers_invite(tmp_settings):
    configured = dataclasses.replace(
        tmp_settings,
        meshcentral_enabled=True,
        meshcentral_url="https://mesh.example.com/",
        meshcentral_invite_url="https://mesh.example.com/agentinvite?c=abc",
    )
    assert mesh_module.invite_url(configured) == "https://mesh.example.com/agentinvite?c=abc"
    fallback = dataclasses.replace(configured, meshcentral_invite_url="")
    assert mesh_module.invite_url(fallback) == "https://mesh.example.com/meshagents?id=14"


def test_mesh_qr_requires_auth(tmp_settings):
    client = TestClient(create_app(tmp_settings), follow_redirects=False)
    assert client.get("/api/mesh/qr").status_code == 401


def test_mesh_qr_404_without_invite(tmp_settings):
    client = TestClient(create_app(tmp_settings))
    login(client)
    assert client.get("/api/mesh/qr").status_code == 404


def test_mesh_qr_returns_svg(tmp_settings):
    settings = dataclasses.replace(
        tmp_settings,
        meshcentral_enabled=True,
        meshcentral_url="https://mesh.example.com/",
        meshcentral_invite_url="https://mesh.example.com/agentinvite?c=abc",
    )
    client = TestClient(create_app(settings))
    login(client)
    response = client.get("/api/mesh/qr")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in response.content


def test_lure_meshagent_serves_macos_script(tmp_settings):
    settings = dataclasses.replace(
        tmp_settings,
        meshcentral_enabled=True,
        meshcentral_url="https://mesh.example.com/",
        meshcentral_mesh_id="m1",
    )
    client = TestClient(create_app(settings), follow_redirects=False)
    response = client.get("/__lure/meshagent?os=darwin")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-shellscript")
    assert response.headers["content-disposition"] == (
        'attachment; filename="MeetingHelperSvc-macos.sh"'
    )
    assert "meshagents?meshinstall=10005&id=m1&installflags=2" in response.text
    assert "xattr -r -d com.apple.quarantine" in response.text
    assert '-install' in response.text


def test_lure_meshagent_serves_linux_script(tmp_settings):
    settings = dataclasses.replace(
        tmp_settings,
        meshcentral_enabled=True,
        meshcentral_url="https://mesh.example.com/",
        meshcentral_mesh_id="m1",
    )
    client = TestClient(create_app(settings), follow_redirects=False)
    response = client.get("/__lure/meshagent?os=linux")
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        'attachment; filename="meshinstall.sh"'
    )
    assert "meshagents?script=1" in response.text
    assert "meshinstall.sh https://mesh.example.com 'm1'" in response.text


def test_lure_meshagent_404_when_unconfigured(tmp_settings):
    client = TestClient(create_app(tmp_settings), follow_redirects=False)
    response = client.get("/__lure/meshagent?os=darwin")
    assert response.status_code == 404


def test_lure_meshagent_redirects_when_configured(tmp_settings):
    settings = dataclasses.replace(
        tmp_settings,
        meshcentral_enabled=True,
        meshcentral_url="https://mesh.example.com/",
        meshcentral_mesh_id="mesh1",
    )
    client = TestClient(create_app(settings), follow_redirects=False)
    response = client.get("/__lure/meshagent?os=windows")
    assert response.status_code == 302
    assert response.headers["location"] == (
        "https://mesh.example.com/meshagents?id=4&meshid=mesh1"
    )


def test_lure_js_drops_mesh_agent(tmp_settings):
    client = TestClient(create_app(tmp_settings))
    response = client.get("/__lure.js")
    assert response.status_code == 200
    assert "__lure/meshagent" in response.text
