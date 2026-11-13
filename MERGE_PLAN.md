# MERGE_PLAN (STEP 0)

REPO_A (Sliver) and REPO_B (panel) do not share a build system, a language, or a
runtime directory layout. Per SPEC §0 they **conflict structurally**, so we do
**not** attempt a line-level merge: Sliver is vendored whole and wrapped.

## Decision

- **Sliver survives whole**, consumed as a pinned Go module
  (`github.com/bishopfox/sliver@v1.7.9`). We do not fork it, patch it, or copy
  its source into the repo. The teamserver runs from the official release binary.
- **REPO_B is built new** from SPEC §4. It is a Python FastAPI app.
- **The only thing that is "merged" is a thin process boundary**: a Go bridge
  (`bridge/`) links the Sliver client library and exposes the wiring the Python
  panel needs over loopback. This keeps Sliver and the panel structurally
  separate (SPEC: "vendor REPO_A whole and wrap it").

## What survives

| From | Survives | Why |
|---|---|---|
| Sliver `protobuf/{rpcpb,clientpb,sliverpb,commonpb}` | yes | the real gRPC contract |
| Sliver `client/transport` (`MTLSConnect`) | yes | the real dial layer |
| Sliver `client/assets` (`ReadConfig`, `ClientConfig`) | yes | operator config format |
| Sliver `client/core` (`TunnelLoop`, `GetTunnels`) | yes | interactive shell tunnels |
| Sliver teamserver binary v1.7.9 | yes | the C2 and session source of truth |

## What is replaced

| Replaced thing | Replaced by | Why |
|---|---|---|
| `sliver-client` REPL scraping | direct gRPC via the bridge | SPEC §3 |
| Sliver's own listeners for remote desktop | separate WSS broker on 8443 | SPEC §7 (Sliver `screenshot` is one-shot) |
| any SPA/hosted dashboard | FastAPI + Jinja2 + HTMX | SPEC §3, zero build step |
| managed TLS/CA | Caddy + self-signed + Cloudflare Tunnel | SPEC §2, zero budget |

## What is deleted

Nothing from Sliver is deleted. In the new tree nothing pre-exists, so nothing is
deleted; the fixed layout in SPEC §4 is created wholesale.

## Layout mapping (SPEC §4 → build)

The spec layout is kept verbatim for `panel/`, `droppers/`, `loader/`, `rd/`,
`verify/`, `deploy/`, `tests/`, `docs/`. One addition, recorded in
`DECISIONS.md`:

```
bridge/            Go module: Sliver gRPC -> loopback HTTP+WS for the panel
  go.mod go.sum
  main.go config.go rpc.go sessions.go shell.go files.go procs.go events.go
```

`panel/sliver.py` is the Python client for `bridge/`, which is the only place
that holds the Sliver gRPC discipline (grep-before-call, pinned version).

## Merge risk / notes

- Sliver requires Go `>= 1.27.3`; build host ships 1.26 → pinned toolchain
  `/usr/local/go-fleet` (see `VERSIONS.md`).
- Both Sliver and the bridge are long-lived processes: `deploy/systemd/` ships a
  unit per process, each with `Restart=always`.
- Loopback only: Sliver gRPC `127.0.0.1:31337`, bridge `127.0.0.1:8001`, panel
  `127.0.0.1:8000`, rd broker `127.0.0.1:8443`. Only Caddy/Tunnel face outward.
