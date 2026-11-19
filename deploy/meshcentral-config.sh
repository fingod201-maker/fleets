#!/bin/sh
# fleet meshcentral - bootstrap stand-alone server config (idempotent).
set -eu

CONF_DIR=/opt/meshcentral/meshcentral-data
CONF="$CONF_DIR/config.json"
DOMAIN="${MESHCENTRAL_DOMAIN:-mesh.telcom.stream}"

mkdir -p "$CONF_DIR"

rand_key() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
  fi
}

if [ ! -f "$CONF" ]; then
  SESSION_KEY="$(rand_key)"
  DB_KEY="$(rand_key)"
  cat > "$CONF" <<EOF
{
  "settings": {
    "cert": "${DOMAIN}",
    "port": 9443,
    "aliasport": 443,
    "redirport": 0,
    "mpsport": 0,
    "tlsoffload": "127.0.0.1",
    "agentpong": 60,
    "selfupdate": false,
    "lanonly": false,
    "wanonly": true,
    "minify": true,
    "sessionkey": "${SESSION_KEY}",
    "dbencryptkey": "${DB_KEY}"
  },
  "domains": {
    "": {
      "title": "Fleet MeshCentral",
      "title2": "Fleet",
      "newAccounts": false,
      "userQuota": 1048576,
      "meshQuota": 248576,
      "passwordRequirements": {
        "min": 12,
        "max": 128,
        "upper": 1,
        "lower": 1,
        "numeric": 1,
        "nonalpha": 1
      },
      "certUrl": "https://${DOMAIN}/",
      "agentConfig": [
        "webSocketMaskOverride=1"
      ]
    }
  }
}
EOF
fi

chmod 600 "$CONF"