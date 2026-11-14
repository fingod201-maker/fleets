#!/bin/sh
# Start Sliver listeners at boot so the C2 survives a reboot unattended.
# 8888 = classic mTLS, 443 = durable externally-reachable mTLS (OCI allows 443),
# 8889 = HTTP(S) listener behind the Cloudflare tunnel (c2http.<zone>).
set -u

CLIENT="/opt/panel/bin/sliver-client"
CFG="/opt/panel/panel.cfg"
[ -x "$CLIENT" ] || exit 0
[ -f "$CFG" ] || exit 0

RC="$(mktemp)"
cat > "$RC" <<'EOF'
mtls --lport 8888
http --lport 8889 --lhost 127.0.0.1
mtls --lport 443 --lhost 0.0.0.0
exit
EOF

for _ in 1 2 3; do
    timeout 60 "$CLIENT" --config "$CFG" --rc "$RC" >/dev/null 2>&1 && break
    sleep 2
done
rm -f "$RC"
exit 0
