# VERSIONS

Pinned versions. Never upgrade mid-build.

| Component | Version | Pin location | Notes |
|---|---|---|---|
| Sliver (REPO_A, C2 engine) | `v1.7.9` | `bridge/go.mod` (`github.com/bishopfox/sliver`), `deploy/deploy.sh` | commit `6cf3ad78bdc6eed799186194e6f4e6fd662dcd8c`, compiled 2026-10-25. Teamserver binary downloaded from the official release. |
| Go (build toolchain) | `go1.27.4` | `deploy/deploy.sh`, `bridge/go.mod` (`go 1.27`) | Sliver v1.7.9 declares `go >= 1.27.3`; Oracle Linux `dnf golang` (1.26.x) is too old, so a pinned toolchain under `/usr/local/go-fleet` is used. |
| Python | `3.12` | target host `python3` | panel runtime. Dev host uses the same major version. |
| FastAPI / Starlette / uvicorn | see `panel/requirements.txt` | `panel/requirements.txt` | FastAPI + Jinja2 + HTMX + xterm.js, no SPA build step. |
| ruff | `0.17.x` | CI + `deploy/deploy.sh --check` | Python lint/format gate. |
| shellcheck | `0.11.0` | CI + gate | every shell script passes. |
| golangci-lint | `v2.15.0` | CI + gate | Go gate. |
| cloudflared | host-installed (`/usr/local/bin/cloudflared`) | `deploy/` | EXTRA A. |
| Caddy | `2.x` (dnf / static) | `deploy/` | SPEC §3 TLS terminator. Behind the Cloudflare Tunnel after EXTRA A. |

## Sliver surface deliberately used

Packages, verified by grepping `$(go env GOMODCACHE)/github.com/bishopfox/sliver@v1.7.9`
(see `NOTES.md` for exact signatures):

- `github.com/bishopfox/sliver/protobuf/rpcpb` — `SliverRPCClient` (the gRPC service).
- `github.com/bishopfox/sliver/protobuf/clientpb` — request/response messages.
- `github.com/bishopfox/sliver/protobuf/sliverpb` — session-facing messages + tunnel types.
- `github.com/bishopfox/sliver/protobuf/commonpb` — `Empty`, `Request`, `Response`, `Process`, `File`.
- `github.com/bishopfox/sliver/client/transport` — `MTLSConnect`, `MTLSConnectWithStatus`.
- `github.com/bishopfox/sliver/client/assets` — `ClientConfig`, `ReadConfig`.
- `github.com/bishopfox/sliver/client/core` — `GetTunnels`, `TunnelLoop` (shell tunnels).
- `github.com/bishopfox/sliver/client/constants` — event type strings.

## Toolchain deviations from SPEC (recorded, see DECISIONS.md)

- Deploy host is **Oracle Linux 10.2 / x86_64**, not Oracle Linux 9 / arm64.
