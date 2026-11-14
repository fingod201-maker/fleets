"""Fetch the asset URLs referenced by the mirrored portal page.

Self-contained mirror: parse index.html (src/href/srcset) and any mirrored CSS
(url(...)) and save every referenced file under the output directory. Handles
the portal's relative font paths and skips page links.
"""

from __future__ import annotations

import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122 Safari/537.36"
)
ATTR_RE = re.compile(r'(?:src|href)="([^"]+)"')
SRCSET_RE = re.compile(r'srcset="([^"]+)"')
CSSURL_RE = re.compile(r"url\(([^)]+)\)")
SKIP_SCHEMES = ("data:", "mailto:", "tel:", "javascript:")
ASSET_EXT = (
    ".js",
    ".css",
    ".woff",
    ".woff2",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".json",
    ".map",
    ".txt",
)


def normalize(base: str, ref: str) -> tuple[str, str] | None:
    ref = ref.strip().strip("'\"").split()[0] if ref.strip() else ""
    if not ref or ref.startswith(SKIP_SCHEMES) or ref.startswith("#"):
        return None
    absolute = urllib.parse.urljoin(base, ref)
    parsed = urllib.parse.urlparse(absolute)
    if not parsed.path or parsed.path.endswith("/"):
        return None
    return absolute, parsed.path


def fetch(url: str, path: str, out: Path, downloaded: set[str], base: str) -> None:
    if path in downloaded:
        return
    downloaded.add(path)
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    body: bytes | None = None
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            body = resp.read()
    except OSError:
        # the portal serves fonts under /static/media despite css pointing at
        # /chunks/media; try the fixed path once.
        fixed = path.replace("/chunks/media/", "/static/media/")
        if fixed != path:
            alt = urllib.parse.urljoin(base, fixed)
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(alt, headers={"User-Agent": UA}), timeout=30
                ) as resp:
                    body = resp.read()
                    path = fixed
            except OSError as exc:
                print(f"  miss {path}: {exc}")
                return
        else:
            return
    if body is None:
        return
    target = out / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    if target.suffix == ".css":
        text = body.decode("utf-8", "ignore")
        for ref in CSSURL_RE.findall(text):
            found = normalize(urllib.parse.urljoin(base, path), ref)
            if found:
                fetch(found[0], found[1], out, downloaded, base)


def main() -> int:
    portal, out_dir = sys.argv[1], Path(sys.argv[2])
    base = portal.rstrip("/") + "/"
    html = (out_dir / "index.html").read_text(encoding="utf-8", errors="ignore")
    refs = list(ATTR_RE.findall(html))
    for match in SRCSET_RE.findall(html):
        refs.extend(part.strip().split(" ")[0] for part in match.split(","))

    downloaded: set[str] = set()
    for ref in refs:
        found = normalize(base, ref)
        if found and found[1].endswith(ASSET_EXT):
            fetch(found[0], found[1], out_dir, downloaded, base)
    for extra in ("/favicon.ico", "/robots.txt"):
        found = normalize(base, extra)
        if found:
            fetch(found[0], found[1], out_dir, downloaded, base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
