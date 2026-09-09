param(
    [string]$RobotIp = "192.168.8.254",
    [string]$DeviceId = "DOG-LJG-001",
    [ValidateSet("cpu", "cuda")]
    [string]$FunAsrDevice = "cuda",
    [string]$FunAsrModel = "paraformer-zh-streaming"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DevRoot = Split-Path -Parent $ProjectRoot
$WebRtcRoot = Join-Path $DevRoot "unitree_webrtc_connect"
$Tool = Join-Path $ProjectRoot "tools\go2_wireless_runtime.py"
$KeyFile = Join-Path $DevRoot "go2-wireless-camera\wireless_collector\.go2_aes_key.dpapi"

if (-not (Test-Path -LiteralPath $Tool)) {
    throw "Go2 wireless runtime tool is missing: $Tool"
}
if (-not (Test-Path -LiteralPath $KeyFile)) {
    throw "Encrypted Go2 AES key is missing: $KeyFile"
}

$PythonCommand = Get-Command python -ErrorAction Stop
$Python = $PythonCommand.Source

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

$Names = @("GO2_AES_KEY", "UNITREE_ROBOT_IP", "PYTHONPATH", "PYTHONUTF8")
$Previous = @{}
foreach ($Name in $Names) {
    $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

$SecureKey = Get-Content -LiteralPath $KeyFile | ConvertTo-SecureString
$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
try {
    $PlainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    if ($PlainKey -notmatch '^[0-9A-Fa-f]{32}$') {
        throw "The DPAPI key file decrypted, but it did not contain a valid 32-hex Go2 AES key."
    }

    $env:GO2_AES_KEY = $PlainKey
    $env:UNITREE_ROBOT_IP = $RobotIp
    $env:PYTHONPATH = "$WebRtcRoot;$ProjectRoot"
    $env:PYTHONUTF8 = "1"

    Set-Location -LiteralPath $ProjectRoot
    & $Python $Tool `
        --execute `
        --audio-source go2 `
        --asr-backend funasr-local `
        --funasr-model $FunAsrModel `
        --funasr-device $FunAsrDevice `
        --device-id $DeviceId
    $ExitCode = $LASTEXITCODE
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    $PlainKey = $null
    $SecureKey = $null
    foreach ($Name in $Names) {
        [Environment]::SetEnvironmentVariable($Name, $Previous[$Name], "Process")
    }
}

exit $ExitCode
