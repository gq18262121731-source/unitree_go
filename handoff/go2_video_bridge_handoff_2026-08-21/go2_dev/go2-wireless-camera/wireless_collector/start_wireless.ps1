$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WebRtcRoot = Join-Path (Split-Path -Parent $ProjectRoot) "unitree_webrtc_connect"
$Python = Join-Path $WebRtcRoot ".venv\Scripts\python.exe"
$KeyFile = Join-Path $PSScriptRoot ".go2_aes_key.dpapi"

if (-not (Test-Path $Python)) {
    throw "WebRTC environment is missing. Install unitree_webrtc_connect first."
}

$Wifi = netsh wlan show interfaces
if ($Wifi -notmatch "Go2_57838_34ab40aa") {
    throw "Connect Windows Wi-Fi to Go2_57838_34ab40aa first."
}

if (-not $env:GO2_AES_KEY -and (Test-Path $KeyFile)) {
    $SecureKey = Get-Content -LiteralPath $KeyFile | ConvertTo-SecureString
    $Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
    try {
        $env:GO2_AES_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    }
}

if (-not $env:GO2_AES_KEY) {
    throw "Device key is not configured. Run .\setup_wireless.ps1 first."
}

if ($env:GO2_AES_KEY -notmatch '^[0-9a-fA-F]{32}$') {
    throw "Invalid AES key. Expected 32 hexadecimal characters."
}

Set-Location $PSScriptRoot
& $Python -m uvicorn app:app --host 127.0.0.1 --port 8093
