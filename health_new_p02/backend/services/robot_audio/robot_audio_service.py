from __future__ import annotations

import asyncio
import tempfile
from contextlib import suppress
from enum import Enum
from pathlib import Path
from typing import Any

from backend.services.robot_audio.base import AudioCapture, PlaybackStatus
from backend.services.robot_audio.sink import AudioSink
from backend.services.robot_audio.source import AudioSource


class RobotAudioState(str, Enum):
    IDLE = "idle"
    PLAYING = "playing"
    WAIT_AFTER_PLAYBACK = "wait_after_playback"
    RECORDING = "recording"
    PROCESSING = "processing"
    ERROR = "error"


class RobotAudioServiceError(RuntimeError):
    def __init__(self, *, stage: str, message: str) -> None:
        self.stage = stage
        self.message = str(message or "").strip() or f"robot audio {stage} failed"
        super().__init__(self.message)


class RobotAudioService:
    """Serialize Go2 capture/playback and enforce the half-duplex guard window."""

    def __init__(
        self,
        *,
        source: AudioSource | None,
        sink: AudioSink | None,
        post_playback_silence_ms: int = 500,
        source_initialization_error: str | None = None,
        sink_initialization_error: str | None = None,
    ) -> None:
        if post_playback_silence_ms < 0:
            raise ValueError("post_playback_silence_ms cannot be negative")
        self._source = source
        self._sink = sink
        self._post_playback_silence_seconds = post_playback_silence_ms / 1000
        self._source_status = "configured" if source is not None else "not_configured"
        self._sink_status = "configured" if sink is not None else "not_configured"
        self._source_initialization_error = self._normalize_error(source_initialization_error)
        self._sink_initialization_error = self._normalize_error(sink_initialization_error)
        if self._source_initialization_error:
            self._source_status = "disconnected"
        if self._sink_initialization_error:
            self._sink_status = "disconnected"
        self._state = RobotAudioState.IDLE
        self._last_error = self._source_initialization_error or self._sink_initialization_error
        self._session_lock = asyncio.Lock()
        self._active_task: asyncio.Task[Any] | None = None
        self._last_playback_loop_time: float | None = None

    @property
    def state(self) -> RobotAudioState:
        return self._state

    @property
    def microphone_available(self) -> bool:
        return self._source is not None

    @property
    def speaker_available(self) -> bool:
        return self._sink is not None

    async def status(self) -> dict[str, object]:
        audio_mode = (
            "response_only"
            if self._source_status == self._sink_status == "not_configured"
            else "half_duplex"
        )
        return {
            "go2_microphone": self._source_status,
            "go2_speaker": self._sink_status,
            "audio_mode": audio_mode,
            "state": self._state.value,
            "recording": self._state is RobotAudioState.RECORDING,
            "playing": self._state is RobotAudioState.PLAYING,
            "last_error": self._last_error,
            "post_playback_silence_ms": round(
                self._post_playback_silence_seconds * 1000
            ),
        }

    async def play_audio(self, audio_bytes: bytes, *, timeout_s: float) -> dict[str, object]:
        if not audio_bytes:
            raise ValueError("audio_bytes cannot be empty")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be greater than zero")
        if self._sink is None:
            raise RobotAudioServiceError(
                stage="playback",
                message=self._sink_initialization_error or "Go2 speaker is not configured",
            )

        async with self._session_lock:
            self._begin_operation(RobotAudioState.PLAYING)
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temporary:
                    temporary.write(audio_bytes)
                    temporary_path = Path(temporary.name)
                playback = await self._sink.play(
                    temporary_path,
                    timeout_seconds=timeout_s,
                )
                if playback.status is not PlaybackStatus.FINISHED:
                    raise RobotAudioServiceError(
                        stage="playback",
                        message=playback.error or f"playback ended as {playback.status.value}",
                    )
                self._sink_status = "connected"
                self._last_error = None
                return playback.as_dict()
            except asyncio.CancelledError:
                self._last_error = "playback cancelled"
                raise
            except RobotAudioServiceError as exc:
                self._sink_status = "disconnected"
                self._fail(exc)
                raise
            except Exception as exc:
                self._sink_status = "disconnected"
                error = RobotAudioServiceError(
                    stage="playback",
                    message=str(exc) or exc.__class__.__name__,
                )
                self._fail(error)
                raise error from exc
            finally:
                if temporary_path is not None:
                    with suppress(OSError):
                        temporary_path.unlink()
                self._last_playback_loop_time = asyncio.get_running_loop().time()
                self._finish_operation()

    async def record_once(
        self,
        *,
        max_duration_s: float,
        silence_timeout_s: float,
    ) -> AudioCapture:
        if max_duration_s <= 0:
            raise ValueError("max_duration_s must be greater than zero")
        if silence_timeout_s <= 0:
            raise ValueError("silence_timeout_s must be greater than zero")
        if self._source is None:
            raise RobotAudioServiceError(
                stage="recording",
                message=self._source_initialization_error or "Go2 microphone is not configured",
            )

        async with self._session_lock:
            self._active_task = asyncio.current_task()
            try:
                await self._wait_after_playback()
                self._state = RobotAudioState.RECORDING
                record_once = getattr(self._source, "record_once", None)
                if callable(record_once):
                    capture = await record_once(
                        max_duration_s=max_duration_s,
                        silence_timeout_s=silence_timeout_s,
                    )
                else:
                    capture = await self._source.record(timeout_seconds=max_duration_s)
                self._source_status = "connected"
                self._last_error = None
                return capture
            except asyncio.CancelledError:
                self._last_error = "recording cancelled"
                raise
            except Exception as exc:
                self._source_status = "disconnected"
                error = (
                    exc
                    if isinstance(exc, RobotAudioServiceError)
                    else RobotAudioServiceError(
                        stage="recording",
                        message=str(exc) or exc.__class__.__name__,
                    )
                )
                self._fail(error)
                raise error from exc
            finally:
                self._finish_operation()

    async def set_processing(self) -> None:
        if self._state in {RobotAudioState.PLAYING, RobotAudioState.RECORDING}:
            raise RobotAudioServiceError(
                stage="processing",
                message=f"cannot process while audio state is {self._state.value}",
            )
        self._state = RobotAudioState.PROCESSING

    async def cancel(self) -> None:
        active = self._active_task
        current = asyncio.current_task()
        if active is not None and active is not current and not active.done():
            active.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await active
        self._active_task = None
        self._state = RobotAudioState.IDLE

    def reset_processing(self) -> None:
        if self._state is RobotAudioState.PROCESSING:
            self._state = RobotAudioState.IDLE

    async def _wait_after_playback(self) -> None:
        if self._last_playback_loop_time is None:
            return
        elapsed = asyncio.get_running_loop().time() - self._last_playback_loop_time
        remaining = self._post_playback_silence_seconds - elapsed
        if remaining <= 0:
            return
        self._state = RobotAudioState.WAIT_AFTER_PLAYBACK
        await asyncio.sleep(remaining)

    def _begin_operation(self, state: RobotAudioState) -> None:
        self._active_task = asyncio.current_task()
        self._state = state

    def _finish_operation(self) -> None:
        self._active_task = None
        self._state = RobotAudioState.IDLE

    def _fail(self, error: BaseException) -> None:
        self._state = RobotAudioState.ERROR
        self._last_error = str(error) or error.__class__.__name__

    @staticmethod
    def _normalize_error(value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None
