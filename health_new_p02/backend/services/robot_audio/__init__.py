"""Robot audio device abstractions for the Go2 companion agent."""

from backend.services.robot_audio.base import (
    AudioCapture,
    AudioCaptureError,
    AudioCaptureTimeoutError,
    AudioPlayback,
    AudioSinkState,
    PlaybackStatus,
    utc_now,
)
from backend.services.robot_audio.mock_audio import MockAudioSink, MockAudioSource
from backend.services.robot_audio.sink import AudioSink
from backend.services.robot_audio.source import AudioSource
from backend.services.robot_audio.robot_audio_service import (
    RobotAudioService,
    RobotAudioServiceError,
    RobotAudioState,
)

__all__ = [
    "AudioCapture",
    "AudioCaptureError",
    "AudioCaptureTimeoutError",
    "AudioPlayback",
    "AudioSink",
    "AudioSinkState",
    "AudioSource",
    "MockAudioSink",
    "MockAudioSource",
    "PlaybackStatus",
    "RobotAudioService",
    "RobotAudioServiceError",
    "RobotAudioState",
    "utc_now",
]
