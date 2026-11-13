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

D13. **`GetSystem`/`Beacon` struct bodies taken as read-only.** `[MED]`
Field extraction for a few rarely-used messages (`GetSystem`, `Beacon`) was
partial; only read-only fields are consumed and the partial list is in NOTES.md.
