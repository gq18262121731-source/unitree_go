param(
    [string]$CameraServiceIp = "192.168.8.253",
    [string]$RobotIp = "192.168.8.245",
    [string]$HealthNewUrl = "http://127.0.0.1:8000",
    [string]$ElderId = "elder01_02",
    [ValidateSet("Cherry", "Serena", "Ethan", "Chelsie")]
    [string]$QwenTtsVoice = "Cherry",
    [ValidateRange(1, 65535)]
    [int]$FollowTargetPort = 8766,
    [ValidateRange(1.0, 100.0)]
    [double]$FollowTargetHz = 20.0,
    [ValidateRange(1, 65535)]
    [int]$VideoPort = 8093,
    [ValidateSet("127.0.0.1", "0.0.0.0")]
    [string]$ListenHost = "0.0.0.0",
    [switch]$UwbVerbose,
    [switch]$VerboseProtocolLog,
    [switch]$EnableLowState,
    [switch]$EnableVideoActiveRecovery,
    [switch]$ManualConfirmStart,
    [switch]$NoOpenBrowser
)

$ErrorActionPreference = "Stop"

$ParsedAddress = $null
if (-not [Net.IPAddress]::TryParse($CameraServiceIp, [ref]$ParsedAddress)) {
    throw "CameraServiceIp is not a valid IP address: $CameraServiceIp"
}
if ($ParsedAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork) {
    throw "CameraServiceIp must be an IPv4 address: $CameraServiceIp"
}
if ($ParsedAddress.Equals([Net.IPAddress]::Any) -or $ParsedAddress.Equals([Net.IPAddress]::Loopback)) {
    throw "CameraServiceIp must be computer B's reachable LAN IPv4 address."
}

$Launcher = Join-Path $PSScriptRoot "Start-Go2WirelessRuntime.ps1"
if (-not (Test-Path -LiteralPath $Launcher)) {
    throw "Base Go2 Wireless Runtime launcher is missing: $Launcher"
}

$Names = @(
    "FOLLOW_TARGET_FORWARD_ENABLED",
    "FOLLOW_TARGET_MONITORING_ENABLED",
    "FOLLOW_TARGET_FORWARD_HOST",
    "FOLLOW_TARGET_FORWARD_PORT",
    "FOLLOW_TARGET_FORWARD_HZ",
    "FOLLOW_TARGET_FORWARD_STALE_SECONDS",
    "FOLLOW_TARGET_FORWARD_STATS_INTERVAL_SECONDS",
    "GO2_UWB_VERBOSE",
    "GO2_VERBOSE_PROTOCOL_LOG",
    "GO2_WEBRTC_ENABLE_LOW_STATE",
    "GO2_WEBRTC_ENABLE_SPORT_STATE",
    "GO2_WEBRTC_ENABLE_UWB",
    "GO2_WEBRTC_ENABLE_MULTIPLE_STATE",
    "GO2_WEBRTC_ENABLE_AUDIO"
)
$Previous = @{}
foreach ($Name in $Names) {
    $Previous[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

try {
    $ExistingUwbVerbose = [string]$Previous["GO2_UWB_VERBOSE"]
    $UwbVerboseFromEnvironment = @("1", "true", "yes", "on") -contains $ExistingUwbVerbose.Trim().ToLowerInvariant()
    $ResolvedUwbVerbose = $UwbVerbose.IsPresent -or $UwbVerboseFromEnvironment
    $ExistingProtocolVerbose = [string]$Previous["GO2_VERBOSE_PROTOCOL_LOG"]
    $ProtocolVerboseFromEnvironment = @("1", "true", "yes", "on") -contains $ExistingProtocolVerbose.Trim().ToLowerInvariant()
    $ResolvedProtocolVerbose = $VerboseProtocolLog.IsPresent -or $ProtocolVerboseFromEnvironment
    $env:FOLLOW_TARGET_FORWARD_ENABLED = "true"
    $env:FOLLOW_TARGET_MONITORING_ENABLED = "true"
    $env:FOLLOW_TARGET_FORWARD_HOST = $CameraServiceIp
    $env:FOLLOW_TARGET_FORWARD_PORT = "$FollowTargetPort"
    $env:FOLLOW_TARGET_FORWARD_HZ = "$FollowTargetHz"
    $env:FOLLOW_TARGET_FORWARD_STALE_SECONDS = "1.0"
    $env:FOLLOW_TARGET_FORWARD_STATS_INTERVAL_SECONDS = "10"
    $env:GO2_UWB_VERBOSE = if ($ResolvedUwbVerbose) { "1" } else { "0" }
    $env:GO2_VERBOSE_PROTOCOL_LOG = if ($ResolvedProtocolVerbose) { "1" } else { "0" }
    # The formal video + UWB companion profile does not consume LowState.
    # Re-enable it only for an explicit diagnostic launch.
    $env:GO2_WEBRTC_ENABLE_LOW_STATE = if ($EnableLowState) { "true" } else { "false" }
    $env:GO2_WEBRTC_ENABLE_SPORT_STATE = "false"
    $env:GO2_WEBRTC_ENABLE_UWB = "false"
    $env:GO2_WEBRTC_ENABLE_MULTIPLE_STATE = "false"
    $env:GO2_WEBRTC_ENABLE_AUDIO = "false"

    Write-Host "Starting one shared Go2 Runtime: video + motion + UWB target UDP."
    Write-Host "Video relay       : $ListenHost`:$VideoPort"
    Write-Host "Follow target UDP : $CameraServiceIp`:$FollowTargetPort at $FollowTargetHz Hz"
    Write-Host "Video                 : ON"
    Write-Host "Audio                 : STANDBY"
    Write-Host "UWB                   : STANDBY"
    Write-Host "SportState            : STANDBY"
    Write-Host "MultiState            : STANDBY"
    Write-Host "LowState subscription : $(if ($EnableLowState) { 'ON' } else { 'OFF' })"
    Write-Host "Video active recovery  : $(if ($EnableVideoActiveRecovery) { 'ON (DIAGNOSTIC)' } else { 'OFF' })"
    Write-Host "UWB console detail : $(if ($ResolvedUwbVerbose) { 'ON' } else { 'OFF' })"
    Write-Host "Protocol detail    : $(if ($ResolvedProtocolVerbose) { 'ON' } else { 'OFF' })"

    # Use named-parameter Hashtable splatting. Positional array splatting can
    # bind RobotIp to ListenHost when one launcher invokes the other.
    $LauncherArgs = @{
        RobotIp = $RobotIp
        HealthNewUrl = $HealthNewUrl
        ElderId = $ElderId
        QwenTtsVoice = $QwenTtsVoice
        ListenHost = $ListenHost
        VideoPort = $VideoPort
    }
    if ($NoOpenBrowser) {
        $LauncherArgs.NoOpenBrowser = $true
    }
    if ($ManualConfirmStart) {
        $LauncherArgs.ManualConfirmStart = $true
    }
    if ($EnableVideoActiveRecovery) {
        $LauncherArgs.EnableVideoActiveRecovery = $true
    }
    & $Launcher @LauncherArgs
    $LauncherExitCode = $LASTEXITCODE
}
finally {
    foreach ($Name in $Names) {
        [Environment]::SetEnvironmentVariable($Name, $Previous[$Name], "Process")
    }
}

exit $LauncherExitCode
