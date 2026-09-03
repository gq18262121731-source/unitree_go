from pathlib import Path

import pytest

from tools.go2_webrtc_stability_ab import (
    PROFILES,
    SDK_ROOT,
    ConsentExpiryCounter,
    VIDEO_ONLY_PROFILE,
    _parse_groups,
)


def test_stability_matrix_matches_the_four_requested_ab_groups() -> None:
    assert {
        name: (
            profile.video,
            profile.uwb,
            profile.sport,
            profile.low_state,
            profile.audio_hub,
        )
        for name, profile in PROFILES.items()
    } == {
        "A": (True, True, True, True, False),
        "B": (True, True, True, False, False),
        "C": (False, True, True, False, False),
        "D": (True, False, True, False, False),
    }


def test_group_parser_normalizes_and_deduplicates() -> None:
    assert _parse_groups("a, C,a") == ["A", "C"]
    assert _parse_groups("v") == ["V"]
    with pytest.raises(Exception, match="unknown groups"):
        _parse_groups("E")


def test_video_only_acceptance_profile_has_no_data_subscriptions() -> None:
    assert (
        VIDEO_ONLY_PROFILE.video,
        VIDEO_ONLY_PROFILE.uwb,
        VIDEO_ONLY_PROFILE.sport,
        VIDEO_ONLY_PROFILE.low_state,
        VIDEO_ONLY_PROFILE.multiple_state,
        VIDEO_ONLY_PROFILE.audio_hub,
    ) == (True, False, False, False, False, False)


def test_tool_discovers_the_sibling_webrtc_sdk_source() -> None:
    assert SDK_ROOT.name == "unitree_webrtc_connect"
    assert (SDK_ROOT / "unitree_webrtc_connect" / "__init__.py").is_file()


def test_powershell_launcher_uses_the_sdk_venv_and_encrypted_key() -> None:
    launcher = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "Start-Go2WebRTCStabilityAB.ps1"
    ).read_text(encoding="utf-8")

    assert '".venv312\\Scripts\\python.exe"' in launcher
    assert ".go2_aes_key.dpapi" in launcher
    assert '$env:PYTHONPATH = "$WebRtcRoot;$ProjectRoot"' in launcher
    assert "--execute" in launcher


def test_evidence_counter_tracks_consent_and_local_signaling_by_group() -> None:
    counter = ConsentExpiryCounter()
    counter.active_group = "B"
    counter.emit(
        __import__("logging").LogRecord(
            "aioice.ice", 30, __file__, 1, "Consent to send expired", (), None
        )
    )
    counter.emit(
        __import__("logging").LogRecord(
            "runtime",
            30,
            __file__,
            1,
            "WEBRTC_RECONNECT_FAILED error=LocalSignalingPortError",
            (),
            None,
        )
    )

    assert counter.count("B") == 1
    assert counter.local_signaling_count("B") == 1
