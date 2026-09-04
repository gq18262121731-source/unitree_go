[CmdletBinding()]
param(
    [string]$RiskEventsPath = "",

    [string]$Interface = "",

    [string]$Distribution = "Ubuntu-20.04",

    [switch]$RestGateway
)

$ErrorActionPreference = "Stop"

function ConvertTo-WslPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if ($Path.StartsWith("/")) {
        return $Path
    }

    $resolvedPath = (Resolve-Path -LiteralPath $Path).Path
    if ($resolvedPath -notmatch '^([A-Za-z]):\\(.*)$') {
        throw "Unsupported Windows path for WSL: $resolvedPath"
    }

    $drive = $Matches[1].ToLowerInvariant()
    $tail = $Matches[2].Replace("\", "/")
    return "/mnt/$drive/$tail"
}

function ConvertTo-Utf8Base64 {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Value
    )

    return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($Value))
}

$launcherName = if ($RestGateway) {
    "start_go2_companion_gateway_real.sh"
} else {
    "start_go2_companion_real.sh"
}
$launcherWindowsPath = Join-Path $PSScriptRoot $launcherName
$launcher = ConvertTo-WslPath $launcherWindowsPath

if ([string]::IsNullOrWhiteSpace($RiskEventsPath)) {
    $wslRiskPath = ""
} elseif ($RiskEventsPath.StartsWith("/")) {
    $wslRiskPath = $RiskEventsPath
} else {
    $wslRiskPath = ConvertTo-WslPath $RiskEventsPath
}

$launcherBase64 = ConvertTo-Utf8Base64 $launcher
$payloadLines = @(
    "set -e",
    "rm -f -- '/tmp/go2-companion-launch-$PID.sh'",
    "launcher=`$(printf '%s' '$launcherBase64' | base64 -d)",
    "set --"
)

if (-not [string]::IsNullOrWhiteSpace($wslRiskPath)) {
    $riskPathBase64 = ConvertTo-Utf8Base64 $wslRiskPath
    $payloadLines += "risk_path=`$(printf '%s' '$riskPathBase64' | base64 -d)"
    $payloadLines += 'set -- "$@" --risk-events "$risk_path"'
}
if (-not [string]::IsNullOrWhiteSpace($Interface)) {
    $interfaceBase64 = ConvertTo-Utf8Base64 $Interface
    $payloadLines += "interface_name=`$(printf '%s' '$interfaceBase64' | base64 -d)"
    $payloadLines += 'set -- "$@" --interface "$interface_name"'
}
$payloadLines += 'exec bash "$launcher" "$@"'

# Windows PowerShell 5.1 can corrupt non-ASCII arguments passed directly to
# wsl.exe. Transport the complete UTF-8 shell payload as ASCII Base64 instead.
# The payload is written to a temporary script so the launched console keeps
# the user's terminal stdin for interactive START/STOP commands.
$payload = ($payloadLines -join "`n") + "`n"
$payloadBase64 = ConvertTo-Utf8Base64 $payload
$bootstrap = "printf '%s' '$payloadBase64' | base64 -d > '/tmp/go2-companion-launch-$PID.sh'; exec bash '/tmp/go2-companion-launch-$PID.sh'"

if ($RestGateway) {
    Write-Host "Launching the real Go2 REST Gateway runtime in $Distribution."
    Write-Host "The process starts IDLE on port 8090; START remains a separate safety-gated API action."
} else {
    Write-Host "Launching the real Go2 companion runtime in $Distribution."
    Write-Host "The process starts IDLE; type START only after the field safety gate is satisfied."
}
& wsl.exe -d $Distribution -- bash -lc $bootstrap
exit $LASTEXITCODE
