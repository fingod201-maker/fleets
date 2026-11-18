# stayawake.ps1 — T5 keep the target awake and reachable with the lid closed.
#
# Windows: standby timeout 0, lid action 0 (do nothing), hibernate off,
# plus powercfg requestsoverride so the rd helper / bridge holds a display
# required request. ES_SYSTEM_REQUIRED|ES_CONTINUOUS is set by the helper
# itself via SetThreadExecutionState (Win32 API, not scriptable from PS).
#
# Requires Administrator. Run: powershell -ExecutionPolicy Bypass -File stayawake.ps1
$ErrorActionPreference = "Continue"

# Never sleep while on AC or battery.
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change standby-timeout-dc 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-dc 0 | Out-Null

# Lid close = do nothing.
powercfg /setacvalueindex scheme_current sub_buttons lidaction 0 | Out-Null
powercfg /setdcvalueindex scheme_current sub_buttons lidaction 0 | Out-Null
powercfg /setschematicon scheme_current >$null 2>&1

# Hibernate off.
powercfg /hibernate off | Out-Null

# Allow the rd helper / bridge to keep the system awake (display required).
powercfg /requestsoverride callocator Display System | Out-Null
powercfg /requestsoverride [PROCESS] rd-win.exe Display System | Out-Null
powercfg /requestsoverride [SERVICE] * Display System | Out-Null

powercfg /setactive scheme_current | Out-Null
Write-Output "stayawake applied: standby/lid/hibernate disabled, requestsoverride set"
