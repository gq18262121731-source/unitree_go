param(
    [string]$RobotIp = "192.168.8.245",
    [string]$GatewayAddress = "192.168.8.254",
    [ValidateRange(1, 65535)]
    [int]$VideoPort = 8093,
    [switch]$ConfigureFirewall,
    [switch]$NoOpenBrowser,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$FrozenLauncher = Join-Path $PSScriptRoot "Start-Go2WirelessRuntime-2026-09-02.ps1"

if (-not (Test-Path -LiteralPath $FrozenLauncher)) {
    throw "Frozen 2026-09-02 launcher is missing: $FrozenLauncher"
}

try {
    $ParsedRobotAddress = [Net.IPAddress]::Parse($RobotIp)
    $ParsedGatewayAddress = [Net.IPAddress]::Parse($GatewayAddress)
}
catch {
    throw "-RobotIp and -GatewayAddress must be valid IPv4 addresses."
}
if (
    $ParsedRobotAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
    $ParsedGatewayAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork
) {
    throw "-RobotIp and -GatewayAddress must be IPv4 addresses."
}

$AssignedGatewayAddress = Get-NetIPAddress `
    -AddressFamily IPv4 `
    -IPAddress $GatewayAddress `
    -ErrorAction SilentlyContinue
if ($null -eq $AssignedGatewayAddress) {
    throw (
        "This computer does not currently own $GatewayAddress. " +
        "Connect the machine-dog computer to the robot WLAN first."
    )
}

$RobotReady = $false
$Probe = [Net.Sockets.TcpClient]::new()
try {
    $ConnectTask = $Probe.ConnectAsync($RobotIp, 9991)
    $RobotReady = $ConnectTask.Wait(3000) -and $Probe.Connected
}
catch {
    $RobotReady = $false
}
finally {
    $Probe.Dispose()
}
if (-not $RobotReady) {
    throw "Go2 WebRTC signaling is not reachable at $RobotIp`:9991."
}

if ($PreflightOnly) {
    Write-Host "2026-09-02 behavior preflight passed: gateway=http://$GatewayAddress`:$VideoPort robot=$RobotIp`:9991 decoder=Python3.12"
    return
}

if ($ConfigureFirewall) {
    $Principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "-ConfigureFirewall requires an Administrator PowerShell window."
    }
    $RuleName = "Robot Video Gateway TCP $VideoPort"
    if (-not (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule `
            -DisplayName $RuleName `
            -Direction Inbound `
            -Action Allow `
            -Protocol TCP `
            -LocalPort $VideoPort `
            -RemoteAddress LocalSubnet `
            -Profile Any | Out-Null
        Write-Host "Created Windows Firewall rule: $RuleName"
    }
}

Write-Host ""
Write-Host "Starting Robot Video Gateway: 2026-09-02 recovery behavior + Python 3.12 decoder"
Write-Host "Go2: $RobotIp`:9991"
Write-Host "Machine-dog computer: http://$GatewayAddress`:$VideoPort"
Write-Host "Video: http://$GatewayAddress`:$VideoPort/stream.mjpg"
Write-Host "Status: http://$GatewayAddress`:$VideoPort/status"
Write-Host ""

$Parameters = @{
    RobotIp = $RobotIp
    ListenHost = "0.0.0.0"
    VideoPort = $VideoPort
    AutoDemo = "none"
}
if ($NoOpenBrowser) {
    $Parameters["NoOpenBrowser"] = $true
}

& $FrozenLauncher @Parameters
exit $LASTEXITCODE
