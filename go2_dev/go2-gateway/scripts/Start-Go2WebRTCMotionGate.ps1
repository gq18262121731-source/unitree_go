param(
    [ValidateSet("ReadOnly", "Stop", "ForwardPulse")]
    [string]$Stage = "ReadOnly",
    [string]$RobotIp = "192.168.8.252",
    [ValidateRange(0.20, 0.23)]
    [double]$Speed = 0.23,
    [ValidateRange(0.20, 0.50)]
    [double]$Duration = 0.40
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
Import-Module (Join-Path $PSHOME "Modules\Microsoft.PowerShell.Security") -ErrorAction Stop

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DevRoot = Split-Path -Parent $ProjectRoot
$WebRtcRoot = Join-Path $DevRoot "unitree_webrtc_connect"
$Python = Join-Path $WebRtcRoot ".venv312\Scripts\python.exe"
$Tool = Join-Path $ProjectRoot "tools\go2_webrtc_motion_gate.py"
$KeyFile = Join-Path $DevRoot "go2-wireless-camera\wireless_collector\.go2_aes_key.dpapi"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "WebRTC Python 3.12 environment is missing: $Python"
}
if (-not (Test-Path -LiteralPath $KeyFile)) {
    throw "Encrypted Go2 device key is missing: $KeyFile"
}
if (Get-NetTCPConnection -LocalPort 8093 -State Listen -ErrorAction SilentlyContinue) {
    throw "Port 8093 is active. Stop the WebRTC video bridge before opening the motion Gate."
}
if (-not (Test-NetConnection $RobotIp -Port 9991 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    throw "Go2 WebRTC signaling is not reachable at $RobotIp`:9991."
}

$StageArgument = switch ($Stage) {
    "ReadOnly" { "readonly" }
    "Stop" { "stop" }
    "ForwardPulse" { "forward-pulse" }
}

$SecureKey = Get-Content -LiteralPath $KeyFile | ConvertTo-SecureString
$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
$PreviousPythonPath = $env:PYTHONPATH
try {
    $env:GO2_AES_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    $env:PYTHONPATH = "$WebRtcRoot;$ProjectRoot"
    $Arguments = @(
        $Tool,
        "--robot-ip", $RobotIp,
        "--stage", $StageArgument,
        "--speed", "$Speed",
        "--duration", "$Duration"
    )
    if ($Stage -ne "ReadOnly") {
        $Arguments += "--execute"
    }
    & $Python @Arguments
    $ToolExitCode = $LASTEXITCODE
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    Remove-Item Env:GO2_AES_KEY -ErrorAction SilentlyContinue
    if ($null -eq $PreviousPythonPath) {
        Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    }
    else {
        $env:PYTHONPATH = $PreviousPythonPath
    }
}

exit $ToolExitCode
