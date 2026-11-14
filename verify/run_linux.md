# Linux attempt run

1. Build a fresh artifact. Download `invoice-<rand4>.tar.gz` from `dl.<zone>`.
2. `tar xzf invoice-<rand4>.tar.gz && ./install.sh` on Ubuntu 24.04 / Fedora 41
   / Debian 12.
3. The dropper installs the implant to `~/.local/bin`, `chmod +x`, starts it with
   `setsid`, installs a systemd **user** unit, `enable --now`, and
   `loginctl enable-linger`, then opens the decoy PDF ~1.5s later.
4. A session appears within 60s.

Remote desktop on Linux needs an X server. On a headless box run it under
`wlheadless-run -- xwayland-run -- ...` (see REPORT/M4); with no `DISPLAY` the
helper connects view-only.
