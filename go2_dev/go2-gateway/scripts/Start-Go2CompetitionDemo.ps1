param(
    [string]$RobotIp = "192.168.8.252",
    [ValidateRange(1, 65535)]
    [int]$VideoPort = 8093,
    [string]$TtsVoice = "Microsoft Huihui Desktop",
    [switch]$NoOpenBrowser
)

$ErrorActionPreference = "Stop"
$RuntimeLauncher = Join-Path $PSScriptRoot "Start-Go2WirelessRuntime.ps1"
if (-not (Test-Path -LiteralPath $RuntimeLauncher)) {
    throw "Unified Runtime launcher is missing: $RuntimeLauncher"
}

$Parameters = @{
    RobotIp = $RobotIp
    ListenHost = "0.0.0.0"
    VideoPort = $VideoPort
    AutoDemo = "phone_demo"
    TtsVoice = $TtsVoice
}
if ($NoOpenBrowser) {
    $Parameters.NoOpenBrowser = $true
}

& $RuntimeLauncher @Parameters
exit $LASTEXITCODE
