param(
    [string]$RobotIp = "192.168.8.252",
    [ValidatePattern("^(?i:[ABCDV](,[ABCDV])*)$")]
    [string]$TestGroups = "A,B,C,D",
    [ValidateRange(1.0, 86400.0)]
    [double]$DurationSeconds = 600.0,
    [ValidateRange(0.1, 60.0)]
    [double]$SampleIntervalSeconds = 0.5,
    [ValidateRange(0.0, 300.0)]
    [double]$CooldownSeconds = 5.0
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSHOME "Modules\Microsoft.PowerShell.Security") -ErrorAction Stop

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DevRoot = Split-Path -Parent $ProjectRoot
$WebRtcRoot = Join-Path $DevRoot "unitree_webrtc_connect"
$Python = Join-Path $WebRtcRoot ".venv312\Scripts\python.exe"
$Tool = Join-Path $ProjectRoot "tools\go2_webrtc_stability_ab.py"
$KeyFile = Join-Path $DevRoot "go2-wireless-camera\wireless_collector\.go2_aes_key.dpapi"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "WebRTC Python 3.12 environment is missing: $Python"
}
if (-not (Test-Path -LiteralPath $Tool)) {
    throw "WebRTC stability tool is missing: $Tool"
}
if (-not (Test-Path -LiteralPath $KeyFile)) {
    throw "Encrypted Go2 device key is missing: $KeyFile"
}

$TcpClient = [Net.Sockets.TcpClient]::new()
try {
    $ConnectTask = $TcpClient.ConnectAsync($RobotIp, 9991)
    if (-not ($ConnectTask.Wait(3000) -and $TcpClient.Connected)) {
        throw "Go2 WebRTC signaling is not reachable at $RobotIp`:9991."
    }
}
finally {
    $TcpClient.Dispose()
}

$SecureKey = Get-Content -LiteralPath $KeyFile | ConvertTo-SecureString
$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
$Names = @("GO2_AES_KEY", "PYTHONPATH", "PYTHONUTF8")
$Previous = @{}
$ToolExitCode = 0
foreach ($Name in $Names) {
    $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

try {
    $env:GO2_AES_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    $env:PYTHONPATH = "$WebRtcRoot;$ProjectRoot"
    $env:PYTHONUTF8 = "1"
    & $Python $Tool `
        --execute `
        --robot-ip $RobotIp `
        --groups $TestGroups `
        --duration-seconds $DurationSeconds `
        --sample-interval-seconds $SampleIntervalSeconds `
        --cooldown-seconds $CooldownSeconds
    $ToolExitCode = $LASTEXITCODE
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    foreach ($Name in $Names) {
        if ($null -eq $Previous[$Name]) {
            Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
        }
        else {
            [Environment]::SetEnvironmentVariable(
                $Name,
                $Previous[$Name],
                "Process"
            )
        }
    }
}

if ($ToolExitCode -ne 0) {
    throw "WebRTC stability tool failed with exit code $ToolExitCode."
}
