param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$KeyFile = Join-Path $PSScriptRoot ".go2_aes_key.dpapi"

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

if ((Test-Path -LiteralPath $KeyFile) -and -not $Force) {
    throw "A DPAPI key file already exists. Use -Force only after confirming replacement is intended."
}

Write-Host "Enter the 32-hex target Go2 AES device key locally."
Write-Host "The value will not be echoed or added to PowerShell command history."
$SecureKey = Read-Host "Target Go2 AES key" -AsSecureString
$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
try {
    $PlainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    if ($PlainKey -notmatch '^[0-9A-Fa-f]{32}$') {
        throw "The device key must contain exactly 32 hexadecimal characters."
    }
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    $PlainKey = $null
}

$EncryptedKey = $SecureKey | ConvertFrom-SecureString
Set-Content -LiteralPath $KeyFile -Value $EncryptedKey -Encoding ASCII
Protect-KeyFileAcl -Path $KeyFile
$SecureKey = $null
$EncryptedKey = $null

$Item = Get-Item -LiteralPath $KeyFile
$Owner = (Get-Acl -LiteralPath $KeyFile).Owner
Write-Host "The target Go2 device key was protected with Windows DPAPI."
Write-Host "Key file: $($Item.FullName)"
Write-Host "Owner: $Owner"
Write-Host "Run test_go2_device_key_dpapi.ps1 before starting the bridge."
