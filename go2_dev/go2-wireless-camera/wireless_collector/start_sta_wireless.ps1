param(
    [switch]$NoOpenBrowser
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONWARNINGS = "ignore"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WebRtcRoot = Join-Path (Split-Path -Parent $ProjectRoot) "unitree_webrtc_connect"
$Python = Join-Path $WebRtcRoot ".venv312\Scripts\python.exe"
$KeyFile = Join-Path $PSScriptRoot ".go2_aes_key.dpapi"
$RobotIp = if ($env:GO2_WEBRTC_IP) { $env:GO2_WEBRTC_IP } else { "192.168.8.248" }
$Port = 8093

if (-not (Test-Path $Python)) {
    throw "Python 3.12 WebRTC environment is missing."
}
if (-not (Test-Path $KeyFile)) {
    throw "Device key is missing. Run setup_wireless.ps1 first."
}

$Existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Existing) {
    if (-not $NoOpenBrowser) {
        Start-Process "http://127.0.0.1:$Port/"
    }
    Write-Host "Go2 wireless video is already running."
    exit 0
}

$SignalPort = Test-NetConnection $RobotIp -Port 9991 -InformationLevel Quiet -WarningAction SilentlyContinue
if (-not $SignalPort) {
    throw "Go2 is not reachable at $RobotIp. Confirm both devices are connected to E5576-822_D7E5."
}

$SecureKey = Get-Content -LiteralPath $KeyFile | ConvertTo-SecureString
$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
try {
    $env:GO2_AES_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
}

$env:GO2_WEBRTC_MODE = "sta"
$env:GO2_WEBRTC_IP = $RobotIp
$Stdout = Join-Path $env:TEMP "go2-sta-webrtc-stdout.log"
$Stderr = Join-Path $env:TEMP "go2-sta-webrtc-stderr.log"
Remove-Item -LiteralPath $Stdout, $Stderr -Force -ErrorAction SilentlyContinue

Start-Process -FilePath $Python `
    -ArgumentList "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "$Port" `
    -WorkingDirectory $PSScriptRoot `
    -RedirectStandardOutput $Stdout `
    -RedirectStandardError $Stderr `
    -WindowStyle Hidden
Remove-Item Env:GO2_AES_KEY -ErrorAction SilentlyContinue

$Deadline = (Get-Date).AddSeconds(35)
do {
    Start-Sleep -Seconds 2
    try {
        $Status = Invoke-RestMethod "http://127.0.0.1:$Port/status" -TimeoutSec 2
        if ($Status.data.hasFrame) {
            if (-not $NoOpenBrowser) {
                Start-Process "http://127.0.0.1:$Port/"
            }
            Write-Host "Go2 wireless video is online at http://127.0.0.1:$Port/"
            exit 0
        }
    }
    catch {
    }
} while ((Get-Date) -lt $Deadline)

$Reason = if ($Status.data.lastError) { $Status.data.lastError } else { Get-Content -LiteralPath $Stderr -Raw -ErrorAction SilentlyContinue }
throw "Go2 wireless video did not start: $Reason"
