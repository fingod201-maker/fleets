# macOS attempt run

1. Build a fresh artifact. Download `<Doc>_<rand4>.command.zip` from `dl.<zone>`.
2. Unzip and double-click the `.command` on a clean VM (macOS 15 arm64, macOS 14
   amd64). Terminal.app opens and runs the script.
3. The first double-click shows Terminal's "are you sure you want to open it?"
   Gatekeeper dialog — click **Open** once (one-time approval). The script then:
   - strips quarantine from itself,
   - attempts `spctl --add` whitelist (fails without root),
   - stay-awake via `caffeinate` + `pmset`,
   - shows a native macOS admin-auth dialog to elevate to root; if granted, runs
     `install-daemon.sh` fully (`spctl --add` whitelist + LaunchDaemon +
     stay-awake). If declined, falls back to a per-user LaunchAgent.
   - launches the implant, opens the decoy PDF, then self-destructs.
4. A session appears within 60s.

Notes:

- macOS has **no loader**; XProtect is hash-based and a fresh build is unknown
  to it. The first-run Gatekeeper check can still show a one-time prompt.
  Silent first-launch requires Apple Developer ID + notarization (not available).
- `LSUIElement` is not applicable (`.command` runs in Terminal); there is no
  Dock icon for the implant itself.
- If input does not work in the remote desktop drawer, grant **Accessibility**
  to the rd helper (the helper reports `needs_accessibility` and the panel shows
  a banner).
