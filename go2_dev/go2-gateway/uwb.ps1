param(
    [int]$Port = 8050,
    [string]$StatusUrl = "http://127.0.0.1:8093/api/v1/robot/companion/status",
    [switch]$Debug,
    [switch]$NoOpenBrowser,
    [switch]$Stop
)

$ErrorActionPreference = "Stop"
$Python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "UWB Dashboard Python not found: $Python"
}

if ($Stop) {
    $DashboardProcesses = @(
        Get-CimInstance Win32_Process |
            Where-Object {
                $_.CommandLine -and
                $_.CommandLine -like "*go2_uwb_telemetry.py*" -and
                (
                    $_.CommandLine -notmatch "(?i)--port\s+\d+" -or
                    $_.CommandLine -match "(?i)--port\s+$Port(\s|$)"
                ) -and
                $_.ProcessId -ne $PID
            }
    )
    foreach ($ProcessInfo in $DashboardProcesses) {
        Stop-Process -Id $ProcessInfo.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped UWB Dashboard PID=$($ProcessInfo.ProcessId)"
    }
    if ($DashboardProcesses.Count -eq 0) {
        Write-Host "UWB Dashboard is not running."
    }
    exit 0
}

try {
    $RuntimeStatus = Invoke-RestMethod -Uri $StatusUrl -Method Get -TimeoutSec 2
    if ($RuntimeStatus.success -eq $false) {
        throw "status endpoint returned success=false"
    }
}
catch {
    throw "Go2 Wireless Runtime is not reachable at $StatusUrl. Start .\start.ps1 first, then run .\uwb.ps1. Detail: $($_.Exception.Message)"
}

$ExistingListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($ExistingListener) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$Port" -Method Get -TimeoutSec 2 | Out-Null
        Write-Host "UWB Dashboard is already running at http://127.0.0.1:$Port"
        exit 0
    }
    catch {
        throw "TCP port $Port is already occupied by another service."
    }
}

$Arguments = @(
    "$PSScriptRoot\tools\go2_uwb_telemetry.py",
    "--wireless",
    "--status-url", $StatusUrl,
    "--port", "$Port"
)
if ($Debug) {
    $Arguments += "--debug"
}
if ($NoOpenBrowser) {
    $Arguments += "--no-open-browser"
}

& $Python @Arguments
exit $LASTEXITCODE
