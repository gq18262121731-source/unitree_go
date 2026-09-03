param(
    [string]$Email = "",
    [string]$SerialNumber = "",
    [ValidateSet("cn", "global")]
    [string]$Region = "cn",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"
$env:PYTHONWARNINGS = "ignore"

function Protect-KeyFileAcl {
    param([Parameter(Mandatory = $true)][string]$Path)

    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $acl = [System.Security.AccessControl.FileSecurity]::new()
    $acl.SetOwner($identity.User)
    $acl.SetAccessRuleProtection($true, $false)
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $identity.User,
        [System.Security.AccessControl.FileSystemRights]::FullControl,
        [System.Security.AccessControl.AccessControlType]::Allow
    )
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
}

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$WebRtcRoot = Join-Path (Split-Path -Parent $ProjectRoot) "unitree_webrtc_connect"
$Python312 = Join-Path $WebRtcRoot ".venv312\Scripts\python.exe"
$PythonLegacy = Join-Path $WebRtcRoot ".venv\Scripts\python.exe"
$Python = if (Test-Path -LiteralPath $Python312) { $Python312 } else { $PythonLegacy }
$FetchScript = Join-Path $PSScriptRoot "fetch_device_key_portable.py"
$KeyFile = Join-Path $PSScriptRoot ".go2_aes_key.dpapi"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python bridge environment is missing. Rebuild .venv312 first."
}
if (-not (Test-Path -LiteralPath $FetchScript)) {
    throw "Portable device-key fetch helper is missing."
}
if ((Test-Path -LiteralPath $KeyFile) -and -not $Force) {
    throw "A DPAPI key file already exists. Use -Force only after confirming replacement is intended."
}

if (-not $Email) {
    $Email = Read-Host "Unitree Go App account bound to the target Go2"
}
if (-not $SerialNumber) {
    $SerialNumber = Read-Host "Target Go2 serial number (local input; do not post it to chat)"
}
if (-not $Email) {
    throw "The Unitree Go App account is required."
}
if ($SerialNumber -notmatch '^[A-Za-z0-9_-]{6,64}$') {
    throw "The target Go2 serial number format is invalid."
}

$SecurePassword = Read-Host "Unitree Go App password" -AsSecureString
$PasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecurePassword)
$PasswordPlain = $null
$RawKey = $null
$Process = $null
$ProcessInfo = $null
try {
    $PasswordPlain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($PasswordPointer)

    $ProcessInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $ProcessInfo.FileName = $Python
    $ProcessInfo.ArgumentList.Add($FetchScript)
    $ProcessInfo.UseShellExecute = $false
    $ProcessInfo.CreateNoWindow = $true
    $ProcessInfo.RedirectStandardOutput = $true
    $ProcessInfo.RedirectStandardError = $true
    $ProcessInfo.Environment["UNITREE_ACCOUNT_EMAIL"] = $Email
    $ProcessInfo.Environment["UNITREE_ACCOUNT_PASSWORD"] = $PasswordPlain
    $ProcessInfo.Environment["GO2_DEVICE_SN"] = $SerialNumber
    $ProcessInfo.Environment["GO2_CLOUD_REGION"] = $Region
    $ProcessInfo.Environment["PYTHONUTF8"] = "1"

    $Process = [System.Diagnostics.Process]::new()
    $Process.StartInfo = $ProcessInfo
    if (-not $Process.Start()) {
        throw "Unable to start the local device-key fetch helper."
    }
    $RawKey = $Process.StandardOutput.ReadToEnd().Trim()
    $LookupError = $Process.StandardError.ReadToEnd().Trim()
    $Process.WaitForExit()

    if ($Process.ExitCode -ne 0 -or $RawKey -notmatch '^[0-9A-Fa-f]{32}$') {
        $Reason = if ($LookupError) { $LookupError } else { "No safe error detail was returned." }
        throw "Unitree device-key lookup failed: $Reason"
    }

    $SecureKey = ConvertTo-SecureString $RawKey -AsPlainText -Force
    $EncryptedKey = $SecureKey | ConvertFrom-SecureString
    Set-Content -LiteralPath $KeyFile -Value $EncryptedKey -Encoding ASCII
    Protect-KeyFileAcl -Path $KeyFile
}
finally {
    if ($Process) {
        $Process.Dispose()
    }
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($PasswordPointer)
    $PasswordPlain = $null
    $RawKey = $null
    $SecureKey = $null
    $EncryptedKey = $null
    $SecurePassword = $null
    $ProcessInfo = $null
}

$Item = Get-Item -LiteralPath $KeyFile
$Owner = (Get-Acl -LiteralPath $KeyFile).Owner
Write-Host "The target Go2 device key was protected with Windows DPAPI."
Write-Host "Key file: $($Item.FullName)"
Write-Host "Owner: $Owner"
Write-Host "Run test_go2_device_key_dpapi.ps1 before starting the bridge."
