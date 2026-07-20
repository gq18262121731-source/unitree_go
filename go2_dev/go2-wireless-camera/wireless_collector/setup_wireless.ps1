param(
    [string]$Email = ""
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONWARNINGS = "ignore"
$Host.UI.RawUI.WindowTitle = "Go2 Wireless Setup - NEW"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WebRtcRoot = Join-Path (Split-Path -Parent $ProjectRoot) "unitree_webrtc_connect"
$Python = Join-Path $WebRtcRoot ".venv\Scripts\python.exe"
$KeyFile = Join-Path $PSScriptRoot ".go2_aes_key.dpapi"

if (-not (Test-Path $Python)) {
    throw "WebRTC environment is missing. Install unitree_webrtc_connect first."
}

if (-not $Email) {
    $Email = Read-Host "Unitree account email bound to this Go2"
}
if (-not $Email) {
    throw "Email is required."
}

Write-Host "Enter the Unitree Go App account password. Do not use the QQ mailbox password."
$SecurePassword = Read-Host "Unitree password" -AsSecureString
$PasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePassword)
$KeyOutput = New-TemporaryFile
$ErrorOutput = New-TemporaryFile
try {
    $env:UNITREE_ACCOUNT_EMAIL = $Email
    $env:UNITREE_ACCOUNT_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($PasswordPointer)
    $KeyProcess = Start-Process -FilePath $Python `
        -ArgumentList (Join-Path $PSScriptRoot "fetch_device_key.py") `
        -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $KeyOutput.FullName `
        -RedirectStandardError $ErrorOutput.FullName
    $FetchExitCode = $KeyProcess.ExitCode
    $RawKey = Get-Content -LiteralPath $KeyOutput.FullName -Raw -ErrorAction SilentlyContinue
    $LookupError = Get-Content -LiteralPath $ErrorOutput.FullName -Raw -ErrorAction SilentlyContinue
    $Key = if ($null -eq $RawKey) { "" } else { $RawKey.Trim() }
}
finally {
    Remove-Item Env:UNITREE_ACCOUNT_EMAIL -ErrorAction SilentlyContinue
    Remove-Item Env:UNITREE_ACCOUNT_PASSWORD -ErrorAction SilentlyContinue
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($PasswordPointer)
    Remove-Item -LiteralPath $KeyOutput.FullName -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $ErrorOutput.FullName -Force -ErrorAction SilentlyContinue
}

if ($FetchExitCode -ne 0 -or $Key -notmatch '^[0-9a-fA-F]{32}$') {
    $Reason = if ($LookupError) { $LookupError.Trim() } else { "No error detail was returned." }
    throw "Unitree key lookup failed: $Reason"
}

$Encrypted = ConvertTo-SecureString $Key -AsPlainText -Force | ConvertFrom-SecureString
Set-Content -LiteralPath $KeyFile -Value $Encrypted -Encoding ASCII
Write-Host "The device key was encrypted for the current Windows user."
Write-Host "Next: make sure Go2_57838_34ab40aa is visible, then run .\start_wireless.ps1"
