from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from app.companion.config_loader import CompanionDemoConfig
from app.companion.exceptions import CompanionLifecycleError
from app.companion.models import (
    CompanionLidarStatus,
    CompanionMotionStatus,
    CompanionRiskStatus,
    CompanionStatus,
    CompanionUwbStatus,
)
from app.companion.supervisor import CompanionSupervisor
from app.config import Settings
from app.follow.controller import (
    FollowController,
    SafetyGuard,
    SafetyGuardConfig,
)
from app.follow.executor import RealMotionSafetyLimit
from app.follow.planner import FollowTargetPlanner
from app.follow.uwb_input import (
    UwbBearingSource,
    UwbBearingUnit,
    UwbInputConfig,
    UwbInputValidator,
)
from app.motion.arbiter import MotionArbiter, MotionArbiterConfig
from app.motion.lidar_safety import LidarSafetyGuard, LidarSafetyLevel
from app.motion.real_follow_executor import (
    RealFollowExecutionStatus,
    RealFollowExecutor,
    RealFollowExecutorConfig,
)
from app.motion.supervised_loop import RawUwbSample, SupervisedMotionLoop
from app.providers.unitree.phase7_input_stream import Phase7ReadonlyInputStream
from app.services.robot_service import RobotService


class CompanionInputStream(Protocol):
    def start(self) -> None: ...

    def close(self) -> None: ...

    def diagnostics(self) -> dict[str, object]: ...


class RiskFeed(Protocol):
    def poll(self, loop: SupervisedMotionLoop, *, now_monotonic: float) -> None: ...

    def diagnostics(self) -> dict[str, object]: ...


class NullRiskFeed:
    def poll(self, loop: SupervisedMotionLoop, *, now_monotonic: float) -> None:
        return None

    def diagnostics(self) -> dict[str, object]:
        return {"source": "mock_input", "accepted": None, "rejected": 0}


class DisabledRiskFeed:
    """Explicit no-risk-input mode; it never manufactures a safe heartbeat."""

    def poll(self, loop: SupervisedMotionLoop, *, now_monotonic: float) -> None:
        return None

    def diagnostics(self) -> dict[str, object]:
        return {
            "source": "disabled",
            "attached": False,
            "fall_preemption_available": False,
            "accepted": None,
            "rejected": 0,
        }


class JsonlRiskFeed:
    """Tail only risk events appended after this runtime starts."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        if not self.path.is_file():
            raise CompanionLifecycleError(
                "RISK_NOT_READY",
                f"risk event feed does not exist: {self.path}",
                503,
            )
        self._offset = self.path.stat().st_size
        self._buffer = ""
        self.accepted = 0
        self.rejected = 0

    def poll(self, loop: SupervisedMotionLoop, *, now_monotonic: float) -> None:
        size = self.path.stat().st_size
        if size < self._offset:
            loop.set_emergency(True, reason="risk_feed_truncated")
            raise CompanionLifecycleError(
                "RISK_NOT_READY", "risk event feed was truncated", 503
            )
        if size == self._offset:
            return
        with self.path.open("r", encoding="utf-8") as stream:
            stream.seek(self._offset)
            chunk = stream.read()
            self._offset = stream.tell()
        self._buffer += chunk
        lines = self._buffer.split("\n")
        self._buffer = lines.pop()
        for line in lines:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("risk event must be a JSON object")
                accepted = loop.ingest_risk_event(
                    payload, received_monotonic=now_monotonic
                )
                self.accepted += int(accepted)
                self.rejected += int(not accepted)
            except Exception:
                self.rejected += 1
                loop.set_emergency(True, reason="invalid_risk_event")
                raise

    def diagnostics(self) -> dict[str, object]:
        return {
            "source": "jsonl",
            "path": str(self.path),
            "accepted": self.accepted,
            "rejected": self.rejected,
        }


class MockCompanionInputStream:
    """Safe deterministic samples for the gateway's explicit Mock mode."""

    def __init__(
        self,
        loop: SupervisedMotionLoop,
        *,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.loop = loop
        self._clock = monotonic_clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="companion-mock-input", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)

    def diagnostics(self) -> dict[str, object]:
        return {
            "started": bool(self._thread and self._thread.is_alive()),
            "source": "mock",
            "samples": self._samples,
            "dds_publishers": 0,
        }

    def _run(self) -> None:
        cloud = [(2.5, -1.0 + index * 0.1, 0.0) for index in range(21)]
        while not self._stop.is_set():
            now = self._clock()
            try:
                self.loop.ingest_uwb(
                    RawUwbSample(
                        distance_est=2.0,
                        orientation_est=-0.55,
                        enabled_from_app=1,
                        error_state=0,
                        sample_monotonic=now,
                    )
                )
                self.loop.ingest_lidar(
                    cloud, frame_id="cloud_base", sample_monotonic=now
                )
                self.loop.ingest_risk_event(
                    {
                        "event_type": "NON_FALL",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                    received_monotonic=now,
                )
                self._samples += 1
            except Exception as exc:
                self.loop.set_emergency(True, reason=f"mock_input_error:{exc}")
                return
            self._stop.wait(0.05)


def build_companion_loop(
    robot_service: RobotService,
    settings: Settings,
    config: CompanionDemoConfig,
    *,
    monotonic_clock: Callable[[], float] = time.monotonic,
) -> SupervisedMotionLoop:
    profile = config.follow
    return SupervisedMotionLoop(
        uwb_validator=UwbInputValidator(
            UwbInputConfig(
                bearing_source=UwbBearingSource(settings.uwb_bearing_source),
                bearing_unit=UwbBearingUnit(settings.uwb_bearing_unit),
                bearing_sign=settings.uwb_bearing_sign,
                bearing_zero_offset_rad=settings.uwb_bearing_zero_offset_rad,
                calibration_confirmed=True,
            )
        ),
        planner=FollowTargetPlanner(
            profile.follow_offset(),
            lost_timeout_seconds=profile.uwb_timeout_seconds,
            monotonic_clock=monotonic_clock,
        ),
        controller=FollowController(
            profile.controller_config(simulation_mode=False),
            SafetyGuard(
                SafetyGuardConfig(
                    min_distance=profile.min_distance,
                    uwb_timeout_seconds=profile.uwb_timeout_seconds,
                )
            ),
        ),
        lidar_guard=LidarSafetyGuard(config.lidar, monotonic_clock=monotonic_clock),
        arbiter=MotionArbiter(
            MotionArbiterConfig(
                uwb_timeout_seconds=profile.uwb_timeout_seconds,
                external_risk_timeout_seconds=2.0,
                require_external_risk_feed=settings.phase7_require_external_risk_feed,
            ),
            monotonic_clock=monotonic_clock,
        ),
        executor=RealFollowExecutor(
            robot_service,
            config=RealFollowExecutorConfig(
                execution_enabled=(
                    settings.mode == "mock"
                    or settings.phase7_motion_execution_enabled
                ),
                command_duration_seconds=0.10,
                max_frequency_hz=5.0,
                continuous_velocity_refresh=True,
            ),
            limits=RealMotionSafetyLimit(
                max_vx=profile.vx_max,
                max_vy=0.0,
                max_wz=profile.wz_max,
            ),
            monotonic_clock=monotonic_clock,
        ),
        companion_supervisor=CompanionSupervisor(
            config=config.companion,
            profile=profile,
            monotonic_clock=monotonic_clock,
        ),
        monotonic_clock=monotonic_clock,
    )


class CompanionRuntime:
    """Own one Phase 7 input/control loop and its fail-closed worker."""

    def __init__(
        self,
        *,
        robot_service: RobotService,
        settings: Settings,
        config: CompanionDemoConfig,
        loop: SupervisedMotionLoop | None = None,
        inputs: CompanionInputStream | None = None,
        risk_feed: RiskFeed | None = None,
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.robot_service = robot_service
        self.settings = settings
        self.config = config
        self._clock = monotonic_clock
        self.loop = loop or build_companion_loop(
            robot_service, settings, config, monotonic_clock=monotonic_clock
        )
        self.inputs = inputs or self._default_inputs()
        self.risk_feed = risk_feed or self._default_risk_feed()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._inputs_started = False
        self._active = False
        self._failure: str | None = None
        self._last_result = None
        self._last_sent_motion: tuple[float, float, float, str] | None = None
        self._lock = threading.RLock()

    def _default_inputs(self) -> CompanionInputStream:
        if self.settings.mode == "mock":
            return MockCompanionInputStream(self.loop, monotonic_clock=self._clock)
        return Phase7ReadonlyInputStream(self.loop, monotonic_clock=self._clock)

    def _default_risk_feed(self) -> RiskFeed:
        if self.settings.mode == "mock":
            return NullRiskFeed()
        risk_path = self.settings.companion_risk_events_path.strip()
        if risk_path:
            return JsonlRiskFeed(risk_path)
        if self.settings.phase7_require_external_risk_feed:
            raise CompanionLifecycleError(
                "RISK_NOT_READY",
                "GO2_COMPANION_RISK_EVENTS_PATH is required in real mode",
                503,
            )
        return DisabledRiskFeed()

    @property
    def failed(self) -> bool:
        return self._failure is not None

    def start_inputs(self) -> None:
        with self._lock:
            if self._inputs_started:
                return
            self.robot_service.safe_stop("companion:preflight")
            self.inputs.start()
            self._stop.clear()
            self._inputs_started = True
            self._thread = threading.Thread(
                target=self._run, name="companion-supervised-loop", daemon=True
            )
            self._thread.start()

    def wait_until_ready(self, timeout_seconds: float) -> None:
        deadline = self._clock() + timeout_seconds
        last_error = CompanionLifecycleError(
            "UWB_NOT_READY", "UWB input is not ready", 503
        )
        while self._clock() < deadline:
            if self._failure is not None:
                raise CompanionLifecycleError(
                    "COMPANION_RUNTIME_FAILED", self._failure, 500
                )
            if self._stop.is_set():
                raise CompanionLifecycleError(
                    "COMPANION_STATE_CONFLICT",
                    "companion startup was cancelled",
                    409,
                )
            now = self._clock()
            error = self._preflight_error(now_monotonic=now)
            if error is None:
                return
            last_error = error
            if error.code in {
                "FALL_LOCK_ACTIVE",
                "MANUAL_TAKEOVER_ACTIVE",
                "LIDAR_STOP_ACTIVE",
            }:
                raise error
            time.sleep(0.02)
        raise last_error

    def activate(self) -> None:
        with self._lock:
            if self._active:
                return
            if not self._inputs_started:
                raise CompanionLifecycleError(
                    "COMPANION_STATE_CONFLICT",
                    "companion inputs must be started before activation",
                    409,
                )
            if self._failure is not None:
                raise CompanionLifecycleError(
                    "COMPANION_RUNTIME_FAILED", self._failure, 500
                )
            self.loop.start_companion()
            self.loop.arm_for_supervised_test()
            self.loop.authorize_resume()
            self._active = True

    def stop(self, *, reason: str = "companion_stopped") -> None:
        """Stop motion while keeping DDS inputs and the worker alive."""

        self.robot_service.safe_stop(f"companion:stop_requested:{reason}")
        with self._lock:
            supervisor = self.loop.companion_supervisor
            if supervisor is not None:
                supervisor.stop()
            self.loop.shutdown()
            self._active = False
            self._last_sent_motion = None
        self.robot_service.safe_stop(f"companion:stop:{reason}")

    def close(self) -> None:
        """Stop motion and release the persistent DDS/SDK runtime resources."""

        self.robot_service.safe_stop("companion:close_requested")
        with self._lock:
            self._active = False
            self._stop.set()
            self._last_sent_motion = None
            thread = self._thread
        self.inputs.close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        try:
            supervisor = self.loop.companion_supervisor
            if supervisor is not None:
                supervisor.stop()
        finally:
            self.loop.shutdown()
            self.robot_service.safe_stop("companion:closed")
            with self._lock:
                self._inputs_started = False
                self._thread = None

    def resume(self) -> None:
        if self.state() != "WAIT_RESUME":
            raise CompanionLifecycleError(
                "COMPANION_STATE_CONFLICT",
                f"resume is only allowed from WAIT_RESUME, current={self.state()}",
                409,
            )
        error = self._preflight_error()
        if error is not None:
            raise error
        if not self._active:
            self.loop.arm_for_supervised_test()
        self.loop.authorize_resume()
        self._active = True

    def state(self) -> str:
        if self._failure is not None:
            return "SAFE_STOP"
        supervisor = self.loop.companion_supervisor
        if supervisor is None:
            return "ERROR"
        return supervisor.state.value

    def snapshot(self) -> CompanionStatus:
        inputs = self.loop.input_status()
        robot_online = self._robot_online()
        supervisor = self.loop.companion_supervisor
        companion = None if supervisor is None else supervisor.snapshot()
        last = self._last_result
        last_sent_motion = self._last_sent_motion
        if last_sent_motion is None:
            motion = CompanionMotionStatus()
        else:
            sent_vx, sent_vy, sent_wz, sent_authority = last_sent_motion
            motion = CompanionMotionStatus(
                vx=sent_vx,
                vy=sent_vy,
                wz=sent_wz,
                authority=sent_authority,
            )
        uwb = inputs["uwb"]
        lidar = inputs["lidar"]
        risk = inputs["risk"]
        risk_attached = bool(self.settings.companion_risk_events_path.strip())
        risk_state = str(risk["state"]) if risk_attached else (
            str(risk["state"]) if self.settings.mode == "mock" else "DISABLED"
        )
        state = self.state()
        reason = (
            self._failure
            or (None if companion is None else companion.reason)
            or "starting"
        )
        return CompanionStatus(
            state=state,
            reason=reason,
            incident_id=risk.get("incident_id"),
            resume_required=(
                state
                in {
                    "WAIT_RESUME",
                    "SAFE_STOP",
                    "EMERGENCY_STOP",
                    "MONITORING",
                    "RECOVERING",
                }
            ),
            runtime_active=self._active and self._failure is None,
            robot_online=robot_online,
            uwb=CompanionUwbStatus(
                valid=bool(uwb["valid"]),
                age_ms=_milliseconds(uwb.get("age_seconds")),
                enabled_from_app=uwb.get("enabled_from_app"),
                error_state=uwb.get("error_state"),
                distance_m=uwb.get("distance_metres"),
                bearing_rad=uwb.get("bearing_radians"),
                orientation_est_rad=uwb.get("orientation_est_radians"),
                error=uwb.get("error"),
            ),
            lidar=CompanionLidarStatus(
                valid=bool(lidar["valid"]),
                state=str(lidar["level"]),
                age_ms=_milliseconds(lidar.get("age_seconds")),
                reason=str(lidar["reason"]),
                nearest_distance_m=lidar.get("nearest_distance"),
            ),
            risk=CompanionRiskStatus(
                state=risk_state,
                heartbeat_fresh=(
                    bool(risk["heartbeat_fresh"])
                    if risk_attached or self.settings.mode == "mock"
                    else False
                ),
                age_ms=_milliseconds(risk.get("age_seconds")),
                incident_id=risk.get("incident_id"),
                manual_takeover=bool(risk["manual_takeover"]),
                emergency_active=bool(risk["emergency_active"]),
            ),
            motion=motion,
            runtime={
                "input": self.inputs.diagnostics(),
                "risk_feed": self.risk_feed.diagnostics(),
                "failure": self._failure,
                "inputs_started": self._inputs_started,
                "worker_alive": bool(self._thread and self._thread.is_alive()),
                "control": _control_diagnostics(
                    last,
                    controller=self.loop.controller,
                    executor=self.loop.executor,
                    profile=self.config.follow,
                    raw_distance=uwb.get("distance_metres"),
                ),
            },
        )

    def _preflight_error(
        self, now_monotonic: float | None = None
    ) -> CompanionLifecycleError | None:
        status = self.loop.input_status(now_monotonic=now_monotonic)
        uwb = status["uwb"]
        lidar = status["lidar"]
        risk = status["risk"]
        if risk.get("incident_id") is not None or risk.get("state") != "NORMAL":
            return CompanionLifecycleError(
                "FALL_LOCK_ACTIVE", "an active fall incident blocks companion", 409
            )
        if risk.get("emergency_active"):
            return CompanionLifecycleError(
                "FALL_LOCK_ACTIVE",
                str(risk.get("emergency_reason") or "emergency is active"),
                409,
            )
        if risk.get("manual_takeover"):
            return CompanionLifecycleError(
                "MANUAL_TAKEOVER_ACTIVE", "manual takeover is active", 409
            )
        if (
            self.settings.phase7_require_external_risk_feed
            and not risk.get("heartbeat_fresh")
        ):
            return CompanionLifecycleError(
                "RISK_NOT_READY", "external risk heartbeat is not fresh", 503
            )
        if not uwb.get("valid"):
            return CompanionLifecycleError(
                "UWB_NOT_READY",
                str(uwb.get("error") or "UWB input is stale"),
                503,
            )
        if not lidar.get("valid"):
            return CompanionLifecycleError(
                "LIDAR_NOT_READY", str(lidar.get("reason")), 503
            )
        if lidar.get("level") == LidarSafetyLevel.STOP.value:
            return CompanionLifecycleError(
                "LIDAR_STOP_ACTIVE", str(lidar.get("reason")), 409
            )
        return None

    def _run(self) -> None:
        period = 1.0 / min(5.0, self.config.follow.control_frequency_hz)
        next_cycle = self._clock()
        try:
            while not self._stop.is_set():
                now = self._clock()
                self.risk_feed.poll(self.loop, now_monotonic=now)
                result = self.loop.step()
                with self._lock:
                    self._last_result = result
                    if result.execution.status is RealFollowExecutionStatus.SENT:
                        self._last_sent_motion = (
                            result.execution.vx,
                            result.execution.vy,
                            result.execution.wz,
                            result.decision.authority.value,
                        )
                    elif result.execution.status not in {
                        RealFollowExecutionStatus.RATE_LIMITED,
                        RealFollowExecutionStatus.DUPLICATE_DECISION,
                    }:
                        self._last_sent_motion = None
                next_cycle += period
                self._stop.wait(max(0.0, next_cycle - self._clock()))
        except Exception as exc:
            self._failure = f"{type(exc).__name__}: {exc}"
            self._active = False
            self.loop.set_emergency(True, reason="companion_runtime_failed")
            self.robot_service.safe_stop("companion:runtime_failed")
            self.loop.shutdown()

    def _robot_online(self) -> bool:
        try:
            return bool(self.robot_service.status().get("online"))
        except Exception:
            return False


def _milliseconds(value: object) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return max(0, int(round(number * 1000.0)))


def _control_diagnostics(
    result,
    *,
    controller: FollowController,
    executor: RealFollowExecutor,
    profile,
    raw_distance: object,
) -> dict[str, object]:
    candidate = None if result is None else result.candidate
    plan = None if result is None else result.follow_plan
    decision = None if result is None else result.decision
    execution = None if result is None else result.execution
    return {
        "distance_mode": controller.distance_mode.value,
        "distance_reason": controller.motion_reason,
        "distance_raw_m": raw_distance,
        "distance_control_m": None if plan is None else plan.uwb_distance,
        "distance_filter": "none",
        "stop_distance_m": profile.follow_stop_distance,
        "resume_distance_m": profile.follow_start_distance,
        "requested_vx": 0.0 if candidate is None else candidate.vx,
        "requested_vy": 0.0 if candidate is None else candidate.vy,
        "requested_wz": 0.0 if candidate is None else candidate.wz,
        "safe_vx": 0.0 if decision is None else decision.vx,
        "safe_vy": 0.0 if decision is None else decision.vy,
        "safe_wz": 0.0 if decision is None else decision.wz,
        "arbiter_authority": "IDLE" if decision is None else decision.authority.value,
        "arbiter_reason": "not_started" if decision is None else decision.reason,
        "execution_status": "NOT_STARTED" if execution is None else execution.status.value,
        "execution_reason": "not_started" if execution is None else execution.reason,
        "resume_authorized": executor.resume_authorized,
    }
