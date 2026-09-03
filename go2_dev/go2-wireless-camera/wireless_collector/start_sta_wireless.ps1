param(
    [switch]$NoOpenBrowser,
    [switch]$Foreground,
    [ValidateSet("127.0.0.1", "0.0.0.0")]
    [string]$ListenHost = "127.0.0.1",
    [string]$RobotIp = "192.168.8.252"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DevRoot = Split-Path -Parent $ProjectRoot
$UnifiedLauncher = Join-Path $DevRoot "go2-gateway\scripts\Start-Go2WirelessRuntime.ps1"

if (-not (Test-Path -LiteralPath $UnifiedLauncher)) {
    throw "Unified Go2 Wireless Runtime launcher is missing: $UnifiedLauncher"
}

Write-Host "The independent STA video client has been retired."
Write-Host "Starting the single-connection video + motion Runtime instead."
$LaunchParameters = @{
    RobotIp = $RobotIp
    ListenHost = $ListenHost
}
if ($NoOpenBrowser) {
    $LaunchParameters.NoOpenBrowser = $true
}
& $UnifiedLauncher @LaunchParameters
exit $LASTEXITCODE
