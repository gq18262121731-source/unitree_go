param(
    [ValidateSet("W3Clockwise30", "W4ForwardClockwise90", "PhoneDemo")]
    [string]$Stage = "W3Clockwise30",
    [string]$RobotIp = "192.168.8.252"
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSHOME "Modules\Microsoft.PowerShell.Security") -ErrorAction Stop

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DevRoot = Split-Path -Parent $ProjectRoot
$WebRtcRoot = Join-Path $DevRoot "unitree_webrtc_connect"
$Python = Join-Path $WebRtcRoot ".venv312\Scripts\python.exe"
$Tool = Join-Path $ProjectRoot "tools\go2_motion_demo.py"
$KeyFile = Join-Path $DevRoot "go2-wireless-camera\wireless_collector\.go2_aes_key.dpapi"
$W4Sequence = Join-Path $ProjectRoot "configs\webrtc_w4.yaml"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "WebRTC Python 3.12 environment is missing: $Python"
}
if (-not (Test-Path -LiteralPath $KeyFile)) {
    throw "Encrypted Go2 device key is missing: $KeyFile"
}
if (Get-NetTCPConnection -LocalPort 8093 -State Listen -ErrorAction SilentlyContinue) {
    throw "Port 8093 is active. For simultaneous video + motion use Start-Go2WirelessRuntime.ps1; this standalone launcher cannot join another process's WebRTC connection."
}
if (-not (Test-NetConnection $RobotIp -Port 9991 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    throw "Go2 WebRTC signaling is not reachable at $RobotIp`:9991."
}

$StageArguments = switch ($Stage) {
    "W3Clockwise30" {
        @("--action", "turn_clockwise", "--value", "30")
    }
    "W4ForwardClockwise90" {
        @("--sequence", $W4Sequence, "--allow-sequence")
    }
    "PhoneDemo" {
        @("--demo", "phone_demo", "--allow-phone-demo")
    }
}

$SecureKey = Get-Content -LiteralPath $KeyFile | ConvertTo-SecureString
$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
$EnvironmentNames = @(
    "GO2_AES_KEY", "PYTHONPATH", "PYTHONUTF8", "GO2_MODE", "UNITREE_ROBOT_IP",
    "GO2_CONTROL_ENABLED", "GO2_READ_ONLY_MODE", "GO2_MAX_VX", "GO2_MAX_VY",
    "GO2_MAX_WZ", "GO2_CONTROL_WATCHDOG_SECONDS", "GO2_STATE_STALE_SECONDS"
)
$PreviousEnvironment = @{}
foreach ($Name in $EnvironmentNames) {
    $PreviousEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

try {
    $env:GO2_AES_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    $env:PYTHONPATH = "$WebRtcRoot;$ProjectRoot"
    $env:PYTHONUTF8 = "1"
    $env:GO2_MODE = "real"
    $env:UNITREE_ROBOT_IP = $RobotIp
    $env:GO2_CONTROL_ENABLED = "true"
    $env:GO2_READ_ONLY_MODE = "false"
    $env:GO2_MAX_VX = "0.30"
    $env:GO2_MAX_VY = "0.30"
    $env:GO2_MAX_WZ = "0.60"
    $env:GO2_CONTROL_WATCHDOG_SECONDS = "0.5"
    $env:GO2_STATE_STALE_SECONDS = "2.0"

    $Arguments = @(
        $Tool,
        "--transport", "webrtc",
        "--execute"
    ) + $StageArguments
    & $Python @Arguments
    $ToolExitCode = $LASTEXITCODE
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    foreach ($Name in $EnvironmentNames) {
        $Value = $PreviousEnvironment[$Name]
        if ($null -eq $Value) {
            [Environment]::SetEnvironmentVariable($Name, $null, "Process")
        }
        else {
            [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
        }
    }
}

exit $ToolExitCode
