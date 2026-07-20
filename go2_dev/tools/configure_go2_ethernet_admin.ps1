param(
    [string]$InterfaceAlias = "",
    [string]$IPAddress = "192.168.123.222",
    [int]$PrefixLength = 24
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($InterfaceAlias)) {
    $adapter = Get-NetAdapter -ErrorAction Stop |
        Where-Object {
            $_.InterfaceDescription -match "Realtek|Ethernet|GbE" -and
            $_.InterfaceDescription -notmatch "Bluetooth|Hyper-V|Virtual|Wi-Fi|Wireless"
        } |
        Select-Object -First 1
} else {
    $adapter = Get-NetAdapter -Name $InterfaceAlias -ErrorAction Stop
}

if (-not $adapter) {
    throw "Ethernet adapter not found."
}

$InterfaceAlias = $adapter.Name
Write-Host "Using adapter: $InterfaceAlias"

$existing = Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -eq $IPAddress }

if ($existing) {
    Write-Host "$IPAddress/$PrefixLength is already configured."
} else {
    New-NetIPAddress -InterfaceAlias $InterfaceAlias -IPAddress $IPAddress -PrefixLength $PrefixLength | Out-Null
    Write-Host "Configured $IPAddress/$PrefixLength"
}

Write-Host "No default gateway was configured."
Write-Host "Now run: .\tools\check_go2_network.ps1"
