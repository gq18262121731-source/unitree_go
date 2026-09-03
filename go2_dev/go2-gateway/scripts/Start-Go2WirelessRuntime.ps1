param(
    [string]$RobotIp = "192.168.8.252",
    [Alias("VideoBind")]
    [ValidateSet("127.0.0.1", "0.0.0.0")]
    [string]$ListenHost = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$VideoPort = 8093,
    [ValidateSet("none", "phone_demo")]
    [string]$AutoDemo = "none",
    [string]$TtsVoice = "Microsoft Huihui Desktop",
    [string]$HealthNewUrl = "http://127.0.0.1:8000",
    [string]$ElderId = "elder01_02",
    # Leave localized defaults to the UTF-8 Python entry point. Windows
    # PowerShell 5.1 can decode a UTF-8-without-BOM script as the ANSI codepage.
    [string]$ElderName = "",
    [string]$WeatherCity = "",
    [string]$DeviceMac = "",
    [string]$VoiceSessionId = "go2-wireless",
    [switch]$RequireStartupConfirmations,
    [switch]$ManualConfirmStart,
    [switch]$NoOpenBrowser
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSHOME "Modules\Microsoft.PowerShell.Security") -ErrorAction Stop

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DevRoot = Split-Path -Parent $ProjectRoot
$WebRtcRoot = Join-Path $DevRoot "unitree_webrtc_connect"
$Python = Join-Path $WebRtcRoot ".venv312\Scripts\python.exe"
$Tool = Join-Path $ProjectRoot "tools\go2_wireless_runtime.py"
$KeyFile = Join-Path $DevRoot "go2-wireless-camera\wireless_collector\.go2_aes_key.dpapi"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "WebRTC Python 3.12 environment is missing: $Python"
}
if (-not (Test-Path -LiteralPath $KeyFile)) {
    throw "Encrypted Go2 device key is missing: $KeyFile"
}
if (Get-NetTCPConnection -LocalPort $VideoPort -State Listen -ErrorAction SilentlyContinue) {
    try {
        $ExistingStatus = Invoke-RestMethod "http://127.0.0.1:$VideoPort/status" -TimeoutSec 2
    }
    catch {
        throw "Port $VideoPort is active but its owner cannot be identified. Stop it before starting the Runtime."
    }
    if ($ExistingStatus.runtimeId -eq "go2-wireless-runtime") {
        throw "Go2WirelessRuntime is already running at http://127.0.0.1:$VideoPort."
    }
    throw "Legacy video bridge is active on $VideoPort. Stop it before starting the unified Runtime."
}
$TcpClient = [Net.Sockets.TcpClient]::new()
$RobotSignalingReady = $false
try {
    $ConnectTask = $TcpClient.ConnectAsync($RobotIp, 9991)
    $RobotSignalingReady = $ConnectTask.Wait(3000) -and $TcpClient.Connected
}
catch {
    $RobotSignalingReady = $false
}
finally {
    $TcpClient.Dispose()
}
if (-not $RobotSignalingReady) {
    throw "Go2 WebRTC signaling is not reachable at $RobotIp`:9991."
}

$SecureKey = Get-Content -LiteralPath $KeyFile | ConvertTo-SecureString
$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
$Names = @(
    "GO2_AES_KEY", "PYTHONPATH", "PYTHONUTF8", "GO2_MODE", "UNITREE_ROBOT_IP",
    "GO2_CONTROL_ENABLED", "GO2_READ_ONLY_MODE", "GO2_MAX_VX", "GO2_MAX_VY",
    "GO2_MAX_WZ", "GO2_CONTROL_WATCHDOG_SECONDS", "GO2_STATE_STALE_SECONDS",
    "GO2_TTS_VOICE", "GO2_LIDAR_ENABLED"
)
$Previous = @{}
foreach ($Name in $Names) {
    $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

try {
    $env:GO2_AES_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    $env:PYTHONPATH = "$WebRtcRoot;$ProjectRoot"
    $env:PYTHONUTF8 = "1"
    $env:GO2_MODE = "real"
    $env:UNITREE_ROBOT_IP = $RobotIp
    $env:GO2_CONTROL_ENABLED = "true"
    $env:GO2_READ_ONLY_MODE = "false"
    # The gateway hard cap covers MANUAL (0.49 m/s) and the Companion's
    # 20%-raised 0.504 m/s final-command cap.
    $env:GO2_MAX_VX = "0.504"
    $env:GO2_MAX_VY = "0.30"
    # The gateway hard cap covers MANUAL pure turns at 1.32 rad/s. Companion
    # keeps its own lower 1.10 rad/s controller/final-command cap.
    $env:GO2_MAX_WZ = "1.32"
    # 4 Hz control has a 250 ms period. Allow normal WebRTC request latency
    # while retaining a bounded fail-safe when the control worker stalls.
    $env:GO2_CONTROL_WATCHDOG_SECONDS = "1.25"
    $env:GO2_STATE_STALE_SECONDS = "2.0"
    $env:GO2_TTS_VOICE = $TtsVoice
    # Competition runtime scope: UWB + video + voice. Do not initialize or
    # decode LiDAR/point-cloud data on the shared WebRTC connection.
    $env:GO2_LIDAR_ENABLED = "false"
    $Arguments = @(
        $Tool, "--execute", "--host", $ListenHost, "--port", "$VideoPort",
        "--health-new-url", $HealthNewUrl, "--elder-id", $ElderId,
        "--voice-session-id", $VoiceSessionId
    )
    if ($ElderName) {
        $Arguments += @("--elder-name", $ElderName)
    }
    if ($WeatherCity) {
        $Arguments += @("--weather-city", $WeatherCity)
    }
    if (-not $RequireStartupConfirmations) {
        $Arguments += "--skip-startup-confirmations"
    }
    if ($ManualConfirmStart) {
        $Arguments += "--manual-confirm-start"
    }
    if ($DeviceMac) {
        $Arguments += @("--device-mac", $DeviceMac)
    }
    if ($AutoDemo -ne "none") {
        $Arguments += @("--auto-demo", $AutoDemo)
    }
    if ($NoOpenBrowser) {
        $Arguments += "--no-open-browser"
    }
    if ($ListenHost -eq "0.0.0.0") {
        Write-Host "Video relay will listen on all interfaces at TCP $VideoPort."
        Write-Host "Computer B also requires a Windows Firewall inbound allow rule for this port."
    }
    & $Python @Arguments
    $ToolExitCode = $LASTEXITCODE
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    foreach ($Name in $Names) {
        $Value = $Previous[$Name]
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

exit $ToolExitCode
