$ErrorActionPreference = "Stop"
$KeyFile = Join-Path $PSScriptRoot ".go2_aes_key.dpapi"

if (-not (Test-Path -LiteralPath $KeyFile)) {
    throw "The local DPAPI device-key file does not exist."
}

$EncryptedKey = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
$SecureKey = $EncryptedKey | ConvertTo-SecureString
$Pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureKey)
try {
    $PlainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($Pointer)
    $Valid = $PlainKey -match '^[0-9A-Fa-f]{32}$'
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Pointer)
    $PlainKey = $null
    $SecureKey = $null
    $EncryptedKey = $null
}

if (-not $Valid) {
    throw "The DPAPI file decrypted, but it did not contain a valid target Go2 device key."
}

$Item = Get-Item -LiteralPath $KeyFile
[pscustomobject]@{
    Valid = $true
    CurrentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    Owner = (Get-Acl -LiteralPath $KeyFile).Owner
    KeyFile = $Item.FullName
    SizeBytes = $Item.Length
    LastWriteTime = $Item.LastWriteTime
} | Format-List
