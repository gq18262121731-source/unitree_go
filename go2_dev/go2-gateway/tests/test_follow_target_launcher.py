from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "scripts" / "Start-Go2WirelessRuntimeWithFollowTarget.ps1"


def test_wrapper_uses_named_hashtable_splatting_for_base_launcher() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "$LauncherArgs = @{" in source
    assert "RobotIp = $RobotIp" in source
    assert "HealthNewUrl = $HealthNewUrl" in source
    assert "ElderId = $ElderId" in source
    assert "ListenHost = $ListenHost" in source
    assert "VideoPort = $VideoPort" in source
    assert "& $Launcher @LauncherArgs" in source
    assert "& $Launcher @Arguments" not in source


def test_wrapper_uses_current_go2_address_by_default() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert '[string]$RobotIp = "192.168.8.245"' in source


def test_wrapper_keeps_follow_target_environment_configuration() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert '[string]$CameraServiceIp = "192.168.8.253"' in source
    assert '$env:FOLLOW_TARGET_FORWARD_ENABLED = "true"' in source
    assert '$env:FOLLOW_TARGET_MONITORING_ENABLED = "true"' in source
    assert "$env:FOLLOW_TARGET_FORWARD_HOST = $CameraServiceIp" in source
    assert '$env:FOLLOW_TARGET_FORWARD_PORT = "$FollowTargetPort"' in source
    assert '$env:FOLLOW_TARGET_FORWARD_HZ = "$FollowTargetHz"' in source


def test_wrapper_defaults_protocol_details_off_and_supports_debug_switches() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "[switch]$UwbVerbose" in source
    assert "[switch]$VerboseProtocolLog" in source
    assert '"GO2_UWB_VERBOSE"' in source
    assert '"GO2_VERBOSE_PROTOCOL_LOG"' in source
    assert '$env:GO2_UWB_VERBOSE = if ($ResolvedUwbVerbose)' in source
    assert (
        '$env:GO2_VERBOSE_PROTOCOL_LOG = if ($ResolvedProtocolVerbose)'
        in source
    )


def test_wrapper_defaults_start_confirmation_off_and_exposes_debug_switch() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "[switch]$ManualConfirmStart" in source
    assert "if ($ManualConfirmStart)" in source
    assert "$LauncherArgs.ManualConfirmStart = $true" in source


def test_wrapper_disables_low_state_by_default_and_has_explicit_opt_in() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "[switch]$EnableLowState" in source
    assert '"GO2_WEBRTC_ENABLE_LOW_STATE"' in source
    assert (
        '$env:GO2_WEBRTC_ENABLE_LOW_STATE = if ($EnableLowState) '
        '{ "true" } else { "false" }'
    ) in source
    assert (
        'Write-Host "LowState subscription : '
        "$(if ($EnableLowState) { 'ON' } else { 'OFF' })\""
    ) in source


def test_wireless_follow_readiness_does_not_require_disabled_low_state() -> None:
    config = (
        ROOT / "configs" / "webrtc_uwb_follow_3min.yaml"
    ).read_text(encoding="utf-8")

    assert "require_low_state_fresh: false" in config


def test_wrapper_defaults_video_active_recovery_off_and_has_explicit_opt_in() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    assert "[switch]$EnableVideoActiveRecovery" in source
    assert "if ($EnableVideoActiveRecovery)" in source
    assert "$LauncherArgs.EnableVideoActiveRecovery = $true" in source
    assert "Video active recovery" in source


def test_wrapper_starts_base_video_with_optional_layers_in_standby() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")

    for name in (
        "GO2_WEBRTC_ENABLE_SPORT_STATE",
        "GO2_WEBRTC_ENABLE_UWB",
        "GO2_WEBRTC_ENABLE_MULTIPLE_STATE",
        "GO2_WEBRTC_ENABLE_AUDIO",
    ):
        assert f'$env:{name} = "false"' in source
    assert 'Write-Host "Video                 : ON"' in source
    assert 'Write-Host "Audio                 : STANDBY"' in source
    assert 'Write-Host "UWB                   : STANDBY"' in source
    assert 'Write-Host "SportState            : STANDBY"' in source
    assert 'Write-Host "MultiState            : STANDBY"' in source
