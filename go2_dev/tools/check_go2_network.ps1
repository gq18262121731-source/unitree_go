param(
    [string]$InterfaceAlias = "",
    [string[]]$Go2Addresses = @("192.168.123.161", "192.168.123.18", "192.168.123.1")
)

$ErrorActionPreference = "Continue"

Write-Host "== Adapter =="
if ([string]::IsNullOrWhiteSpace($InterfaceAlias)) {
    $adapter = Get-NetAdapter -ErrorAction SilentlyContinue |
        Where-Object {
            $_.InterfaceDescription -match "Realtek|Ethernet|GbE" -and
            $_.InterfaceDescription -notmatch "Bluetooth|Hyper-V|Virtual|Wi-Fi|Wireless"
        } |
        Select-Object -First 1
} else {
    $adapter = Get-NetAdapter -Name $InterfaceAlias -ErrorAction SilentlyContinue
}

if (-not $adapter) {
    Write-Host "Adapter not found. Pass -InterfaceAlias with the Windows Ethernet adapter name."
    exit 2
}

$InterfaceAlias = $adapter.Name

$adapter | Select-Object Name, Status, LinkSpeed, MacAddress, InterfaceDescription | Format-List

Write-Host "== IPv4 =="
$ipConfig = Get-NetIPConfiguration -InterfaceAlias $InterfaceAlias
$ipConfig | Select-Object InterfaceAlias, IPv4Address, IPv4DefaultGateway | Format-List

$hasGo2Subnet = $false
foreach ($addr in $ipConfig.IPv4Address) {
    if ($addr.IPAddress -like "192.168.123.*") {
        $hasGo2Subnet = $true
    }
}

if ($adapter.Status -ne "Up") {
    Write-Host "RESULT: physical Ethernet link is not Up."
    Write-Host "Check Go2 power, Ethernet cable, and the robot Ethernet port."
    exit 1
}

if (-not $hasGo2Subnet) {
    Write-Host "RESULT: adapter is Up, but not on 192.168.123.x."
    Write-Host "Set this adapter to 192.168.123.222 / 255.255.255.0 with no gateway."
}

Write-Host "== Ping common Go2 addresses =="
$reachable = $false
foreach ($target in $Go2Addresses) {
    $ok = Test-Connection -ComputerName $target -Count 1 -Quiet
    Write-Host "$target reachable: $ok"
    if ($ok) {
        $reachable = $true
    }
}

if ($reachable) {
    Write-Host "RESULT: at least one Go2 address is reachable."
    exit 0
}

Write-Host "RESULT: no common Go2 address responded."
exit 1
