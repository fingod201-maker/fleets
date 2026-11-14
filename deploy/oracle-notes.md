# Oracle Cloud notes

Target observed: Oracle Linux **10.2**, **x86_64** (spec said OL9/arm64 — see
`DECISIONS.md` D6). Single Always Free instance, reached as `opc`.

## Ports

| Port | Bind | Purpose |
|---|---|---|
| 443 / 80 | 0.0.0.0 | Caddy (TLS). May be closed after the Cloudflare Tunnel is up. |
| 8888 | 0.0.0.0 | Sliver mTLS listener (implants). |
| 31337 | 127.0.0.1 | Sliver gRPC (operators). |
| 8000 | 127.0.0.1 | panel. |
| 8001 | 127.0.0.1 | bridge. |
| 8443 | 127.0.0.1 | rd broker (helpers connect from the internet). |

## Steps

1. `git clone` the repo on the host, `cd deploy`, `sudo ./deploy.sh`.
   `deploy.sh` installs packages, a pinned Go toolchain, the Sliver release
   binary, builds the bridge + helpers, writes `/etc/panel/env` (600), installs
   systemd units, and prints the panel password exactly once (also POSTed to
   Telegram when `TG_*` are set).
2. Open **ingress 443/tcp and 80/tcp** in the OCI Security List (and 8888/tcp for
   mTLS implants) while Caddy is the public edge. The instance's own
   `firewalld` is opened by `deploy.sh`.
3. Verify: `sudo ./deploy.sh --check`.

## After the Cloudflare Tunnel (EXTRA A)

- `cloudflared` routes `panel`/`get`/`dl` to `127.0.0.1:8000`; `c2.<zone>` stays
  grey-cloud A → origin on `:8888`.
- Once the tunnel is healthy, close **80/443** in both `firewalld` and the OCI
  Security List; the origin is then reachable only through the tunnel (operator
  surface) and `:8888` (implants). Caddy can stay running for direct `sslip.io`
  access or be stopped.
- `c2.<zone>` must be **grey-cloud**; Cloudflare does not pass raw mTLS. TTL 120
  so the origin can be rotated without waiting.

## Instance reclaim

Always Free instances are reclaimed when idle. `verify/` ships a keepalive cron
(burn ~10% of one core for 30s every 5 min) and a 6-hourly Telegram heartbeat
(EXTRA E3).
