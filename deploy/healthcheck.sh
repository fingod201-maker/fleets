#!/usr/bin/env bash
# fleet healthcheck — run every 60s by fleet-healthcheck.timer.
# Three consecutive failures -> Telegram, then restart the stack. Reset on ok.
# shellcheck disable=SC1090,SC2015
set -uo pipefail

ENV_FILE="/etc/panel/env"
STATE="/run/fleet-health.fails"

[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }

tg() {
  [ -n "${TG_BOT_TOKEN:-}" ] || return 0
  curl -s -o /dev/null --max-time 10 -X POST \
    "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TG_CHAT_ID}" \
    --data-urlencode "parse_mode=HTML" --data-urlencode "text=$1" || true
}

fail_reasons=""
check() { # description command...
  local desc="$1"; shift
  if "$@" >/dev/null 2>&1; then return 0; fi
  fail_reasons="${fail_reasons}${desc}; "
  return 1
}

fails=0
[ -f "$STATE" ] && fails="$(cat "$STATE" 2>/dev/null || echo 0)"

status_ok=1
check "panel" curl -fsS --max-time 5 http://127.0.0.1:8000/healthz || status_ok=0
check "bridge" curl -fsS --max-time 5 http://127.0.0.1:8001/healthz || status_ok=0
check "grpc" bash -c "ss -lntH 2>/dev/null | grep -q ':31337'" || status_ok=0
check "tunnel" systemctl is-active --quiet fleet-cloudflared.service || status_ok=0

if [ "$status_ok" = 1 ]; then
  [ "$fails" != 0 ] && tg "🟢 <b>fleet recovered</b>"
  echo 0 > "$STATE"
  exit 0
fi

fails=$((fails + 1))
echo "$fails" > "$STATE"
if [ "$fails" -ge 3 ]; then
  tg "🔴 <b>fleet unhealthy</b> (${fails}x): ${fail_reasons} — restarting"
  # restart only what is actually down: restarting sliver would kill every
  # implant session (and with it HVNC/rd), so never do it unless gRPC is gone.
  curl -fsS --max-time 4 http://127.0.0.1:8000/healthz >/dev/null 2>&1 || systemctl restart fleet-panel.service || true
  curl -fsS --max-time 4 http://127.0.0.1:8001/healthz >/dev/null 2>&1 || systemctl restart fleet-bridge.service || true
  ss -lntH 2>/dev/null | grep -q ':31337' || systemctl restart fleet-sliver.service || true
  echo 0 > "$STATE"
fi
exit 0
