# Architecture

```
            visitors                     operator
               │                            │
        get./us0*web. (lure)          panel.<zone> (Access)
               │                            │
               └──────── cloudflared tunnel ┴──────────────┐
                                     │                       │
                              panel 127.0.0.1:8000  ──HTTP──> bridge 127.0.0.1:8001 ──gRPC──> sliver 127.0.0.1:31337
                                     │                                                              │
                          implants ──mTLS c2.<zone>:8888 (grey-cloud A) ───────────────────────────┘
                          rd helper ──WSS :8443──> panel (outbound from target)
```

- **panel** — FastAPI + Jinja2 + HTMX + xterm.js. Auth, DB, audit, session table,
  drawer (desktop/shell/files/procs/info), build page, audit, health strip.
- **bridge** — Go. The only component that speaks Sliver's gRPC API (pinned,
  grep-verified symbols). Exposes loopback HTTP/WS for the panel.
- **lure** — a static mirror of the Zoom-portal front page, served by the panel
  for the rotating edge domains; a tiny injected script sniffs the OS and
  auto-downloads the matching dropper. All requests land on the same self-signed
  origin, so Domain rotation is just DNS.
- **droppers/loader/rd** — per-OS delivery, Windows hash-loader, rd helpers.
- **deploy** — Caddy, cloudflared, systemd units, `deploy.sh`, `cloudflare.sh`.

## Data flow for a victim

1. Victim opens `us0Xweb.telcom.stream` (rotating alias) → mirrored Zoom page.
2. Injected `/__lure.js` calls `/__lure/target` → returns an OS-specific artifact
   on a **random** alias domain → browser auto-downloads + runs.
3. Dropper drops/installs the implant; it connects out to `c2.<zone>:8888` mTLS.
4. Sliver session → bridge event → panel table within 1s; a Telegram alert with
   IP/country/device/core data fires once.
5. Operator clicks the host → drawer → desktop (rd helper over WSS), shell,
   files, processes, info.

## Security posture

- Only loopback ports 8000/8001/31337/8443 are local; Caddy/Tunnel are the only
  outward surfaces. `c2.` is grey-cloud because Cloudflare does not proxy raw
  mTLS.
- Secrets in `/etc/panel/env` (600). Audit is append-only (DB triggers).
