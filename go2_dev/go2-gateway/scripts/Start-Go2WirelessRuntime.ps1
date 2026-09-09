param(
    [string]$RobotIp = "192.168.8.254",
    [Alias("VideoBind")]
    [ValidateSet("127.0.0.1", "0.0.0.0")]
    [string]$ListenHost = "0.0.0.0",
    [ValidateRange(1, 65535)]
    [int]$VideoPort = 8093,
    [ValidateSet("none", "phone_demo")]
    [string]$AutoDemo = "none",
    [string]$TtsVoice = "Microsoft Huihui Desktop",
    [ValidateSet("Cherry", "Serena", "Ethan", "Chelsie")]
    [string]$QwenTtsVoice = "Cherry",
    [string]$HealthNewUrl = "http://127.0.0.1:8765",
    [string]$ElderId = "elder01_02",
    # Leave localized defaults to the UTF-8 Python entry point. Windows
    # PowerShell 5.1 can decode a UTF-8-without-BOM script as the ANSI codepage.
    [string]$ElderName = "",
    [string]$WeatherCity = "",
    [string]$DeviceMac = "",
    [string]$VoiceSessionId = "go2-wireless",
    [ValidateSet("remote", "funasr-local")]
    [string]$AsrBackend = "funasr-local",
    [string]$FunAsrModel = "paraformer-zh-streaming",
    [string]$FunAsrDevice = "cuda",
    [string]$PythonPath = "",
    [string]$DpapiKeyFile = "",
    # Deprecated: startup confirmation prompts were removed. Runtime safety
    # interlocks, START/STOP checks, and manual debug confirmations remain in
    # the Python runtime.
    [switch]$RequireStartupConfirmations,
    [switch]$ManualConfirmStart,
    [switch]$NoAutoFollow,
    [switch]$NoVoiceDebug,
    [switch]$EnableVideoActiveRecovery,
    [switch]$NoOpenBrowser
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSHOME "Modules\Microsoft.PowerShell.Security") -ErrorAction Stop

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DevRoot = Split-Path -Parent $ProjectRoot
$WebRtcRoot = Join-Path $DevRoot "unitree_webrtc_connect"
$Tool = Join-Path $ProjectRoot "tools\go2_wireless_runtime.py"

function Resolve-RuntimePython {
    if ($PythonPath) {
        if (-not (Test-Path -LiteralPath $PythonPath)) {
            throw "Explicit Python path does not exist: $PythonPath"
        }
        return (Resolve-Path -LiteralPath $PythonPath).Path
    }

    $CondaPrefix = [Environment]::GetEnvironmentVariable("CONDA_PREFIX", "Process")
    $CondaEnv = [Environment]::GetEnvironmentVariable("CONDA_DEFAULT_ENV", "Process")
    if ($CondaPrefix -and ($CondaEnv -eq "torchgpu")) {
        $CondaPython = Join-Path $CondaPrefix "python.exe"
        if (Test-Path -LiteralPath $CondaPython) {
            return (Resolve-Path -LiteralPath $CondaPython).Path
        }
    }

    $TorchGpuCandidates = @(
        (Join-Path $env:USERPROFILE "anaconda3\envs\torchgpu\python.exe"),
        "C:\Users\13010\anaconda3\envs\torchgpu\python.exe"
    )
    foreach ($Candidate in $TorchGpuCandidates) {
        if (Test-Path -LiteralPath $Candidate) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }

    if ($CondaPrefix) {
        $CondaPython = Join-Path $CondaPrefix "python.exe"
        if (Test-Path -LiteralPath $CondaPython) {
            return (Resolve-Path -LiteralPath $CondaPython).Path
        }
    }

    $CommandPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($CommandPython -and $CommandPython.Source) {
        return $CommandPython.Source
    }

    $WebRtcPython = Join-Path $WebRtcRoot ".venv312\Scripts\python.exe"
    if (Test-Path -LiteralPath $WebRtcPython) {
        return (Resolve-Path -LiteralPath $WebRtcPython).Path
    }

    throw "No Python runtime found. Activate conda env torchgpu or pass -PythonPath."
}

function Test-AesKeyFormat {
    param([AllowNull()][string]$Value)
    return [bool]($Value -and (($Value.Trim()) -match '^[0-9A-Fa-f]{32}$'))
}

function Read-DpapiAesKey {
    param([Parameter(Mandatory = $true)][string]$Path)

    $EncryptedKey = $null
    $SecureKey = $null
    $Pointer = [IntPtr]::Zero
    try {
        $EncryptedKey = (Get-Content -LiteralPath $Path -Raw).Trim()
        $SecureKey = $EncryptedKey | ConvertTo-SecureString
        $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
        $PlainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer).Trim()
        if (-not (Test-AesKeyFormat $PlainKey)) {
            throw "invalid format"
        }
        return $PlainKey
    }
    finally {
        if ($Pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
        }
        $PlainKey = $null
        $SecureKey = $null
        $EncryptedKey = $null
    }
}

function Get-DpapiKeyCandidates {
    $Candidates = New-Object System.Collections.Generic.List[string]
    if ($DpapiKeyFile) {
        $Candidates.Add($DpapiKeyFile)
    }

    $LocalCandidates = @(
        (Join-Path $ProjectRoot ".go2_aes_key.dpapi"),
        (Join-Path $ProjectRoot "data\.go2_aes_key.dpapi"),
        (Join-Path $DevRoot "go2-wireless-camera\wireless_collector\.go2_aes_key.dpapi"),
        (Join-Path $DevRoot ".go2_aes_key.dpapi")
    )
    foreach ($Path in $LocalCandidates) {
        $Candidates.Add($Path)
    }

    if (Test-Path -LiteralPath "E:\unitree") {
        Get-ChildItem "E:\unitree" -Filter ".go2_aes_key.dpapi" -Recurse -Force -ErrorAction SilentlyContinue |
            Sort-Object LastWriteTime -Descending |
            ForEach-Object { $Candidates.Add($_.FullName) }
    }

    $Seen = @{}
    foreach ($Candidate in $Candidates) {
        if (-not $Candidate) {
            continue
        }
        $FullPath = $Candidate
        if (Test-Path -LiteralPath $Candidate) {
            $FullPath = (Resolve-Path -LiteralPath $Candidate).Path
        }
        $Key = $FullPath.ToLowerInvariant()
        if (-not $Seen.ContainsKey($Key)) {
            $Seen[$Key] = $true
            $FullPath
        }
    }
}

function Resolve-AesKey {
    $Existing = [Environment]::GetEnvironmentVariable("GO2_AES_KEY", "Process")
    if (Test-AesKeyFormat $Existing) {
        Write-Host "[GO2] AES source=env"
        Write-Host "[GO2] AES valid=yes"
        return $Existing.Trim()
    }
    if ($Existing) {
        Write-Host "[GO2] AES source=env invalid format; trying DPAPI"
    }

    $Failures = New-Object System.Collections.Generic.List[string]
    foreach ($Candidate in Get-DpapiKeyCandidates) {
        if (-not (Test-Path -LiteralPath $Candidate)) {
            continue
        }
        try {
            $Key = Read-DpapiAesKey -Path $Candidate
            Write-Host "[GO2] AES key loaded from DPAPI"
            Write-Host "[GO2] AES key validation passed"
            Write-Host "[GO2] AES source=dpapi"
            Write-Host "[GO2] AES valid=yes"
            return $Key
        }
        catch {
            $Failures.Add("$Candidate :: DPAPI decrypt failed / invalid format")
        }
    }

    if ($Failures.Count -gt 0) {
        $Failures | ForEach-Object { Write-Host "[GO2] AES candidate failed: $_" }
    }
    throw "No valid Go2 AES key found. Set GO2_AES_KEY to 32 hex chars or import .go2_aes_key.dpapi."
}

$Python = Resolve-RuntimePython
$CondaEnv = [Environment]::GetEnvironmentVariable("CONDA_DEFAULT_ENV", "Process")
$PythonEnv = Split-Path -Leaf (Split-Path -Parent $Python)
$ResolvedAesKey = Resolve-AesKey
Write-Host "[ENV] python=$Python"
Write-Host "[ENV] conda_env=$($CondaEnv -replace '^$', 'none')"
Write-Host "[ENV] python_env=$PythonEnv"

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

$Names = @(
    "GO2_AES_KEY", "PYTHONPATH", "PYTHONUTF8", "GO2_MODE", "UNITREE_ROBOT_IP",
    "GO2_CONTROL_ENABLED", "GO2_READ_ONLY_MODE", "GO2_MAX_VX", "GO2_MAX_VY",
    "GO2_MAX_WZ", "GO2_CONTROL_WATCHDOG_SECONDS", "GO2_STATE_STALE_SECONDS",
    "GO2_TTS_VOICE", "GO2_QWEN_TTS_VOICE", "GO2_LIDAR_ENABLED",
    "GO2_WEBRTC_ENABLE_VIDEO_ACTIVE_RECOVERY",
    "GO2_WEBRTC_RECONNECT_ON_STALE",
    "GO2_WEBRTC_RECONNECT_INITIAL_SECONDS",
    "GO2_WEBRTC_RECONNECT_STEP_SECONDS",
    "GO2_WEBRTC_RECONNECT_MAX_SECONDS",
    "GO2_WEBRTC_RECONNECT_STABLE_RESET_SECONDS",
    "GO2_WEBRTC_DISCONNECT_GRACE_SECONDS"
)
$Previous = @{}
foreach ($Name in $Names) {
    $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

try {
    $env:GO2_AES_KEY = $ResolvedAesKey
    $env:PYTHONPATH = "$WebRtcRoot;$ProjectRoot"
    $env:PYTHONUTF8 = "1"
    $env:GO2_MODE = "real"
    $env:UNITREE_ROBOT_IP = $RobotIp
    $env:GO2_CONTROL_ENABLED = "true"
    $env:GO2_READ_ONLY_MODE = "false"
    # Conservative combined profile shared by MANUAL and Companion.
    $env:GO2_MAX_VX = "0.42"
    $env:GO2_MAX_VY = "0.30"
    $env:GO2_MAX_WZ = "0.55"
    # 4 Hz control has a 250 ms period. Allow normal WebRTC request latency
    # while retaining a bounded fail-safe when the control worker stalls.
    $env:GO2_CONTROL_WATCHDOG_SECONDS = "1.25"
    $env:GO2_STATE_STALE_SECONDS = "2.0"
    $env:GO2_TTS_VOICE = $TtsVoice
    $env:GO2_QWEN_TTS_VOICE = $QwenTtsVoice
    # Competition runtime scope: UWB + video + voice. Do not initialize or
    # decode LiDAR/point-cloud data on the shared WebRTC connection.
    $env:GO2_LIDAR_ENABLED = "false"
    # Formal operation keeps a stale video track degraded without toggling
    # Video or replacing the PeerConnection. Opt in only for diagnostics.
    $env:GO2_WEBRTC_ENABLE_VIDEO_ACTIVE_RECOVERY = if ($EnableVideoActiveRecovery) { "true" } else { "false" }
    $env:GO2_WEBRTC_RECONNECT_ON_STALE = "false"
    $env:GO2_WEBRTC_RECONNECT_INITIAL_SECONDS = "2"
    $env:GO2_WEBRTC_RECONNECT_STEP_SECONDS = "2"
    $env:GO2_WEBRTC_RECONNECT_MAX_SECONDS = "15"
    $env:GO2_WEBRTC_RECONNECT_STABLE_RESET_SECONDS = "30"
    $env:GO2_WEBRTC_DISCONNECT_GRACE_SECONDS = "3"
    $Arguments = @(
        $Tool, "--execute", "--host", $ListenHost, "--port", "$VideoPort",
        "--health-new-url", $HealthNewUrl, "--elder-id", $ElderId,
        "--voice-session-id", $VoiceSessionId,
        "--audio-source", "go2",
        "--asr-backend", $AsrBackend,
        "--funasr-model", $FunAsrModel,
        "--funasr-device", $FunAsrDevice
    )
    if (-not $NoAutoFollow) {
        $Arguments += "--xiaokang-auto-follow"
    }
    if (-not $NoVoiceDebug) {
        $Arguments += "--voice-debug"
    }
    if ($ElderName) {
        $Arguments += @("--elder-name", $ElderName)
    }
    if ($WeatherCity) {
        $Arguments += @("--weather-city", $WeatherCity)
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
    $ResolvedAesKey = $null
    foreach ($Name in $Names) {
        $Value = $Previous[$Name]
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

exit $ToolExitCode
