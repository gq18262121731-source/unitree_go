param(
    [Parameter(Mandatory = $true)]
    [string]$RobotIp,
    [int]$Port = 8093,
    [switch]$NoOpenBrowser
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONWARNINGS = "ignore"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WebRtcRoot = Join-Path (Split-Path -Parent $ProjectRoot) "unitree_webrtc_connect"
$Python312 = Join-Path $WebRtcRoot ".venv312\Scripts\python.exe"
$PythonLegacy = Join-Path $WebRtcRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $Python312) { $Python312 } else { $PythonLegacy }
$KeyFile = Join-Path $PSScriptRoot ".go2_aes_key.dpapi"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment is missing. Rebuild .venv312 from the handoff instructions first."
}
if (-not (Test-Path -LiteralPath $KeyFile)) {
    throw "Device key is missing. Run setup_go2_device_key.ps1 or write_go2_device_key_dpapi.ps1 as the intended Windows service user first."
}

$Existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Existing) {
    try {
        $Status = Invoke-RestMethod "http://127.0.0.1:$Port/status" -TimeoutSec 3
        if ($Status.data.hasFrame) {
            Write-Host "Go2 video bridge is already ready at http://127.0.0.1:$Port/"
            exit 0
        }
    }
    catch {
    }
    throw "Port $Port is already occupied, but a ready Go2 video bridge was not detected."
}

$SignalingPorts = @(9991, 8081)
$ReachablePort = $null
foreach ($CandidatePort in $SignalingPorts) {
    if (Test-NetConnection $RobotIp -Port $CandidatePort -InformationLevel Quiet -WarningAction SilentlyContinue) {
        $ReachablePort = $CandidatePort
        break
    }
}
if ($null -eq $ReachablePort) {
    throw "Go2 signaling is unreachable at $RobotIp on TCP 9991 and 8081. Fix the wired IP route first."
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
$Stdout = Join-Path $env:TEMP "go2-webrtc-stdout.log"
$Stderr = Join-Path $env:TEMP "go2-webrtc-stderr.log"
Remove-Item -LiteralPath $Stdout, $Stderr -Force -ErrorAction SilentlyContinue

try {
    Start-Process -FilePath $Python `
        -ArgumentList "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", "$Port" `
        -WorkingDirectory $PSScriptRoot `
        -RedirectStandardOutput $Stdout `
        -RedirectStandardError $Stderr `
        -WindowStyle Hidden
}
finally {
    Remove-Item Env:GO2_AES_KEY -ErrorAction SilentlyContinue
}

$Deadline = (Get-Date).AddSeconds(45)
$Status = $null
do {
    Start-Sleep -Seconds 2
    try {
        $Status = Invoke-RestMethod "http://127.0.0.1:$Port/status" -TimeoutSec 3
        if ($Status.data.hasFrame) {
            if (-not $NoOpenBrowser) {
                Start-Process "http://127.0.0.1:$Port/"
            }
            Write-Host "Go2 video is ready. robot=$RobotIp signaling=$ReachablePort local=http://127.0.0.1:$Port/"
            exit 0
        }
    }
    catch {
    }
} while ((Get-Date) -lt $Deadline)

$Reason = if ($Status -and $Status.data.lastError) {
    $Status.data.lastError
} else {
    Get-Content -LiteralPath $Stderr -Raw -ErrorAction SilentlyContinue
}
throw "Go2 video bridge did not produce a valid frame within 45 seconds: $Reason"
