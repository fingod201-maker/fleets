from panel.lnk import build_lnk, lure_icon


def test_lnk_header_and_clsid():
    blob = build_lnk(r"C:\Windows\System32\cmd.exe", "/c echo hi", "Zoom.ico")
    assert blob[:4] == b"\x4c\x00\x00\x00"
    assert blob[4:20] == bytes(
        [0x01, 0x14, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
         0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x46]
    )
    assert len(blob) > 120


def test_lnk_embeds_arguments():
    blob = build_lnk(r"C:\Windows\System32\cmd.exe", "/c whoami", "")
    assert "/c whoami".encode("utf-16-le") in blob


def test_icon_is_ico():
    ico = lure_icon()
    assert ico[0:2] == b"\x00\x00"      # reserved
    assert ico[2:4] == b"\x01\x00"      # type = icon
    assert ico[4:6] == b"\x01\x00"      # one image
    assert len(ico) > 1000
