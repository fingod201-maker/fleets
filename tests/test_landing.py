import dataclasses

from panel import landing


def art(name, os_name, ts, slug):
    return {"name": name, "os": os_name, "ts": ts, "slug": slug, "sha256": "x"}


def test_sniff():
    assert landing.sniff("Mozilla/5.0 (Windows NT 10.0)") == "windows"
    assert landing.sniff("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)") == "macos"
    assert landing.sniff("Mozilla/5.0 (X11; Linux x86_64)") == "linux"
    assert landing.sniff("curl/8") == "all"


def test_newest_picks_latest_per_family():
    picked = landing.newest(
        [
            art("w1.pdf.vbs", "windows", 1, "s1"),
            art("w2.pdf.vbs", "windows", 5, "s2"),
            art("l.tar.gz", "linux", 2, "s3"),
        ]
    )
    assert picked["windows"]["slug"] == "s2"
    assert picked["linux"]["slug"] == "s3"
    assert "macos" not in picked


def test_page_single_target(tmp_settings):
    settings = dataclasses.replace(tmp_settings, document_name="Q3 Report")
    picked = {"windows": art("Q3_ab.pdf.vbs", "windows", 1, "slugwin")}
    html = landing.page(settings, picked, "Mozilla (Windows NT 10.0)")
    assert "Q3 Report" in html
    assert "/e/slugwin" in html
    assert "Download for Windows" in html


def test_page_unknown_ua_shows_all(tmp_settings):
    picked = {
        "windows": art("w.pdf.vbs", "windows", 1, "sw"),
        "linux": art("invoice.tar.gz", "linux", 1, "sl"),
    }
    html = landing.page(tmp_settings, picked, "curl/8")
    assert "/e/sw" in html and "/e/sl" in html


def test_page_no_artifacts(tmp_settings):
    html = landing.page(tmp_settings, {}, "curl/8")
    assert "no artifacts yet" in html
