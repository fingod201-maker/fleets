# fleet

Unified covert fleet platform: one deployable panel over Sliver, with remote
desktop, per-OS droppers, evasion chain, landing page, Telegram alerting and a
Cloudflare edge. Built to `SPEC.md` + `EXTRA.md`.

## Shape

```
Caddy/Tunnel :443 ──> panel 127.0.0.1:8000 ──HTTP──> bridge 127.0.0.1:8001 ──gRPC──> sliver 127.0.0.1:31337
                                                            │
                                          implants ──mTLS :8888─┘
                                          rd-win/rd-mac/rd-linux ──WSS :8443──> panel
```

- `panel/` — FastAPI + Jinja2 + HTMX + xterm.js operator UI.
- `bridge/` — Go: the only thing that speaks the real Sliver gRPC API.
- `droppers/`, `loader/`, `rd/` — per-OS delivery, Windows loader, rd helpers.
- `deploy/` — Oracle Linux host: Caddy, cloudflared, systemd, `deploy.sh --check`.
- `verify/`, `docs/`, `REPORT.md` — how the claim is measured.

## Read first

`INVENTORY.md`, `MERGE_PLAN.md`, `NOTES.md`, `DECISIONS.md`, `VERSIONS.md`.
