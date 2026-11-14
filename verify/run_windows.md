# Windows attempt run

1. Build a fresh artifact: panel → build, or `panel.build.BuildPipeline` with a
   unique run. Download `<Doc>_<rand4>.pdf.vbs` from `dl.<zone>`.
2. On a clean Win11 24H2 VM (Defender defaults, cloud protection ON), move the
   file to `%USERPROFILE%\Downloads` and double-click it.
3. Observe: the decoy PDF opens ~1.5s later; no SmartScreen dialog (double
   extension hides `.vbs`, `ADODB.Stream.SaveToFile` avoids `Zone.Identifier`).
4. A session with the VM's hostname appears in the panel within 60s (cadence is
   60s ±35% jitter with a 5-minute first-callback delay).

Record: executed, no_dialog, session_60s, then re-check at 24/48/72h.

If SmartScreen appears: check that the file was **downloaded through the lure**
(a mirrored copy from a file-share origin yields fewer warnings than a fresh
domain) and that the extension stayed `.pdf.vbs`.
