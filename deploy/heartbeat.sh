#!/usr/bin/env bash
# fleet heartbeat — every 6h, so a silently dead host becomes visible.
# shellcheck disable=SC1090
set -uo pipefail

ENV_FILE="/etc/panel/env"
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }
[ -n "${TG_BOT_TOKEN:-}" ] || exit 0

up="$(uptime -p 2>/dev/null || uptime)"
disk="$(df -h / | tail -1 | awk '{print $4}')"
tunnel="$(systemctl is-active fleet-cloudflared.service 2>/dev/null || echo unknown)"
implants="$(curl -s --max-time 5 http://127.0.0.1:8001/sliver/sessions | python3 -c 'import sys,json
try:
    print(len(json.load(sys.stdin).get("sessions", [])))
except ValueError:
    print("?")' 2>/dev/null || echo "?")"

curl -s -o /dev/null --max-time 10 -X POST \
  "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT_ID}" \
  --data-urlencode "parse_mode=HTML" \
  --data-urlencode "text=❤️ <b>fleet heartbeat</b>
uptime: ${up}
disk free: ${disk}
tunnel: ${tunnel}
implants: ${implants}" || true
