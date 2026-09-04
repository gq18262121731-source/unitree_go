param(
    [string]$RobotIp = "auto",
    [ValidateRange(1, 65535)]
    [int]$VideoPort = 8093,
    [string]$InterfaceAlias = "WLAN",
    [ValidateRange(3, 300)]
    [int]$RobotReadyTimeoutSeconds = 30,
    [ValidateSet("none", "phone_demo")]
    [string]$AutoDemo = "none",
    [string]$HealthNewUrl = "http://127.0.0.1:8000",
    [string]$ElderId = "elder01_02",
    [string]$ElderName = "",
    [string]$WeatherCity = "",
    [string]$DeviceMac = "",
    [string]$VoiceSessionId = "go2-wireless",
    [switch]$RequireStartupConfirmations,
    [switch]$ManualConfirmStart,
    [switch]$NoOpenBrowser,
    [switch]$ConfigureFirewall,
    [switch]$PreflightOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DevRoot = Split-Path -Parent $ProjectRoot
$Python = Join-Path $DevRoot "unitree_webrtc_connect\.venv312\Scripts\python.exe"
$RuntimeLauncher = Join-Path $PSScriptRoot "Start-Go2WirelessRuntime.ps1"
$MdnsTool = Join-Path $ProjectRoot "tools\robot_video_mdns.py"
$LogDirectory = Join-Path $ProjectRoot "logs"

foreach ($RequiredPath in @($Python, $RuntimeLauncher, $MdnsTool)) {
    if (-not (Test-Path -LiteralPath $RequiredPath)) {
        throw "Required Robot Video Gateway file is missing: $RequiredPath"
    }
}

& $Python -c "import zeroconf" 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Python package 'zeroconf' is missing. Run: & '$Python' -m pip install -r '$ProjectRoot\requirements.txt'"
}

$IpConfiguration = Get-NetIPConfiguration -InterfaceAlias $InterfaceAlias -ErrorAction Stop
$LanIpEntry = @(
    $IpConfiguration.IPv4Address |
        Where-Object {
            $_.IPAddress -and
            $_.IPAddress -notlike "169.254.*" -and
            $_.IPAddress -ne "127.0.0.1"
        }
)[0]
if ($null -eq $LanIpEntry) {
    throw "No usable IPv4 address was found on interface '$InterfaceAlias'."
}
$LanAddress = $LanIpEntry.IPAddress
$LanPrefixLength = [int]$LanIpEntry.PrefixLength
$NetworkName = $IpConfiguration.NetProfile.Name

function Test-SameIPv4Subnet {
    param(
        [string]$FirstAddress,
        [string]$SecondAddress,
        [int]$PrefixLength
    )
    $FirstBytes = [Net.IPAddress]::Parse($FirstAddress).GetAddressBytes()
    $SecondBytes = [Net.IPAddress]::Parse($SecondAddress).GetAddressBytes()
    $BitsRemaining = $PrefixLength
    for ($Index = 0; $Index -lt 4; $Index++) {
        if ($BitsRemaining -ge 8) {
            if ($FirstBytes[$Index] -ne $SecondBytes[$Index]) {
                return $false
            }
            $BitsRemaining -= 8
            continue
        }
        if ($BitsRemaining -gt 0) {
            $Mask = [byte](256 - [Math]::Pow(2, 8 - $BitsRemaining))
            return (($FirstBytes[$Index] -band $Mask) -eq ($SecondBytes[$Index] -band $Mask))
        }
        return $true
    }
    return $true
}

if ($RobotIp -eq "auto") {
    if ($LanPrefixLength -ne 24) {
        throw (
            "Automatic Go2 discovery is limited to the dedicated /24 robot WLAN. " +
            "Interface '$InterfaceAlias' is connected to '$NetworkName' at " +
            "$LanAddress/$LanPrefixLength. Connect to the Go2/router WLAN first."
        )
    }
    $AddressParts = $LanAddress.Split(".")
    $SubnetPrefix = "$($AddressParts[0]).$($AddressParts[1]).$($AddressParts[2])"
    Write-Host "Searching $SubnetPrefix.0/24 for one Go2 WebRTC signaling service ..."
    $ProbeJobs = foreach ($HostNumber in 1..254) {
        $CandidateAddress = "$SubnetPrefix.$HostNumber"
        if ($CandidateAddress -eq $LanAddress) {
            continue
        }
        $Client = [Net.Sockets.TcpClient]::new()
        [pscustomobject]@{
            Address = $CandidateAddress
            Client = $Client
            Task = $Client.ConnectAsync($CandidateAddress, 9991)
        }
    }
    $ScanDeadline = (Get-Date).AddSeconds(4)
    while ((Get-Date) -lt $ScanDeadline -and ($ProbeJobs.Task.IsCompleted -contains $false)) {
        Start-Sleep -Milliseconds 50
    }
    $DetectedRobotAddresses = @()
    foreach ($ProbeJob in $ProbeJobs) {
        try {
            if (
                $ProbeJob.Task.IsCompleted -and
                -not $ProbeJob.Task.IsFaulted -and
                -not $ProbeJob.Task.IsCanceled -and
                $ProbeJob.Client.Connected
            ) {
                $DetectedRobotAddresses += $ProbeJob.Address
            }
        }
        finally {
            $ProbeJob.Client.Dispose()
        }
    }
    if ($DetectedRobotAddresses.Count -eq 0) {
        throw (
            "No Go2 WebRTC signaling service was found on $SubnetPrefix.0/24. " +
            "Check Go2 power and its router/STA connection."
        )
    }
    if ($DetectedRobotAddresses.Count -gt 1) {
        throw (
            "Multiple TCP 9991 services were found: " +
            ($DetectedRobotAddresses -join ", ") +
            ". Start again with -RobotIp <address>."
        )
    }
    $RobotIp = $DetectedRobotAddresses[0]
    Write-Host "Discovered Go2 signaling at $RobotIp`:9991."
}
else {
    try {
        $ParsedRobotAddress = [Net.IPAddress]::Parse($RobotIp)
    }
    catch {
        throw "-RobotIp must be 'auto' or a valid IPv4 address: $RobotIp"
    }
    if ($ParsedRobotAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
        throw "-RobotIp must be an IPv4 address: $RobotIp"
    }
    if (-not (Test-SameIPv4Subnet $LanAddress $RobotIp $LanPrefixLength)) {
        throw (
            "Wrong WLAN for Go2. Interface '$InterfaceAlias' is connected to " +
            "'$NetworkName' at $LanAddress/$LanPrefixLength, but Go2 is $RobotIp. " +
            "Connect the machine-dog computer to the Go2/router WLAN first."
        )
    }
}

$RobotReady = $false
$RobotReadyDeadline = (Get-Date).AddSeconds($RobotReadyTimeoutSeconds)
Write-Host "Waiting for Go2 WebRTC signaling at $RobotIp`:9991 ..."
do {
    $Probe = [Net.Sockets.TcpClient]::new()
    try {
        $ConnectTask = $Probe.ConnectAsync($RobotIp, 9991)
        $RobotReady = $ConnectTask.Wait(1500) -and $Probe.Connected
    }
    catch {
        $RobotReady = $false
    }
    finally {
        $Probe.Dispose()
    }
    if (-not $RobotReady -and (Get-Date) -lt $RobotReadyDeadline) {
        Start-Sleep -Seconds 2
    }
} while (-not $RobotReady -and (Get-Date) -lt $RobotReadyDeadline)

if (-not $RobotReady) {
    throw (
        "Go2 is on the expected subnet but $RobotIp`:9991 did not become ready " +
        "within $RobotReadyTimeoutSeconds seconds. Check Go2 power and its router/STA connection."
    )
}
Write-Host "Go2 signaling is ready."
if ($PreflightOnly) {
    Write-Host "Preflight passed: WLAN=$NetworkName local=$LanAddress/$LanPrefixLength Go2=$RobotIp`:9991"
    return
}

if ($ConfigureFirewall) {
    $Principal = New-Object Security.Principal.WindowsPrincipal(
        [Security.Principal.WindowsIdentity]::GetCurrent()
    )
    if (-not $Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "-ConfigureFirewall requires an Administrator PowerShell window."
    }
    $TcpRuleName = "Robot Video Gateway TCP $VideoPort"
    if (-not (Get-NetFirewallRule -DisplayName $TcpRuleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule `
            -DisplayName $TcpRuleName `
            -Direction Inbound `
            -Action Allow `
            -Protocol TCP `
            -LocalPort $VideoPort `
            -RemoteAddress LocalSubnet `
            -Profile Any | Out-Null
        Write-Host "Created Windows Firewall rule: $TcpRuleName"
    }
    $MdnsRuleName = "Robot Video Gateway mDNS UDP 5353"
    if (-not (Get-NetFirewallRule -DisplayName $MdnsRuleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule `
            -DisplayName $MdnsRuleName `
            -Direction Inbound `
            -Action Allow `
            -Program $Python `
            -Protocol UDP `
            -LocalPort 5353 `
            -RemoteAddress LocalSubnet `
            -Profile Any | Out-Null
        Write-Host "Created Windows Firewall rule: $MdnsRuleName"
    }
}
else {
    Write-Warning "Firewall rules were not changed. Run once from an Administrator PowerShell window with -ConfigureFirewall if LAN clients cannot connect or discover the service."
}

New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$MdnsStdout = Join-Path $LogDirectory "robot-video-mdns-$Timestamp.out.log"
$MdnsStderr = Join-Path $LogDirectory "robot-video-mdns-$Timestamp.err.log"
$MdnsProcess = $null
$RuntimeExitCode = 1

function Stop-ProcessTree {
    param([int]$ProcessId)

    $ChildProcesses = @(
        Get-CimInstance Win32_Process `
            -Filter "ParentProcessId = $ProcessId" `
            -ErrorAction SilentlyContinue
    )
    foreach ($ChildProcess in $ChildProcesses) {
        Stop-ProcessTree -ProcessId ([int]$ChildProcess.ProcessId)
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

try {
    $MdnsProcess = Start-Process `
        -FilePath $Python `
        -ArgumentList @(
            $MdnsTool,
            "--address", $LanAddress,
            "--port", "$VideoPort"
        ) `
        -PassThru `
        -WindowStyle Hidden `
        -RedirectStandardOutput $MdnsStdout `
        -RedirectStandardError $MdnsStderr

    Start-Sleep -Seconds 1
    if ($MdnsProcess.HasExited) {
        $MdnsError = Get-Content -LiteralPath $MdnsStderr -Raw -ErrorAction SilentlyContinue
        throw "mDNS advertisement failed to start. $MdnsError"
    }

    Write-Host ""
    Write-Host "Robot Video Gateway is starting."
    Write-Host "Discovery service: _robot-video._tcp.local"
    Write-Host "Stable local URL: http://robot-gateway.local:$VideoPort"
    Write-Host "Temporary IP URL: http://$LanAddress`:$VideoPort"
    Write-Host "Stream path: /stream.mjpg"
    Write-Host "Status path: /api/v1/video/status"
    Write-Host ""

    $RuntimeParameters = @{
        RobotIp = $RobotIp
        ListenHost = "0.0.0.0"
        VideoPort = $VideoPort
        AutoDemo = $AutoDemo
        HealthNewUrl = $HealthNewUrl
        ElderId = $ElderId
        VoiceSessionId = $VoiceSessionId
    }
    if ($ElderName) {
        $RuntimeParameters["ElderName"] = $ElderName
    }
    if ($WeatherCity) {
        $RuntimeParameters["WeatherCity"] = $WeatherCity
    }
    if ($DeviceMac) {
        $RuntimeParameters["DeviceMac"] = $DeviceMac
    }
    if ($RequireStartupConfirmations) {
        $RuntimeParameters["RequireStartupConfirmations"] = $true
    }
    if ($ManualConfirmStart) {
        $RuntimeParameters["ManualConfirmStart"] = $true
    }
    if ($NoOpenBrowser) {
        $RuntimeParameters["NoOpenBrowser"] = $true
    }

    & $RuntimeLauncher @RuntimeParameters
    $RuntimeExitCode = $LASTEXITCODE
}
finally {
    if ($null -ne $MdnsProcess) {
        # A Windows venv launcher creates a second Python process. Stopping only
        # the launcher leaves an orphaned mDNS advertisement behind.
        Stop-ProcessTree -ProcessId $MdnsProcess.Id
    }
}

exit $RuntimeExitCode
