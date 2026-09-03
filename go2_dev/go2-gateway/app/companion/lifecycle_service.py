from __future__ import annotations

import threading
import json
import os
from pathlib import Path
from typing import Callable, Protocol

from app.companion.config_loader import (
    CompanionDemoConfig,
    load_companion_demo_config,
)
from app.companion.exceptions import CompanionLifecycleError
from app.companion.models import CompanionServiceState, CompanionStatus
from app.companion.runtime import CompanionRuntime
from app.config import Settings
from app.core.errors import GatewayError
from app.services.robot_service import RobotService


class RuntimeContract(Protocol):
    def start_inputs(self) -> None: ...

    def wait_until_ready(self, timeout_seconds: float) -> None: ...

    def activate(self) -> None: ...

    def stop(self, *, reason: str = "companion_stopped") -> None: ...

    def close(self) -> None: ...

    def resume(self) -> None: ...

    def state(self) -> str: ...

    def snapshot(self) -> CompanionStatus: ...


class CompanionLifecycleService:
    """Single-instance lifecycle boundary for the supervised companion loop."""

    _CONTROL_OWNER = "phase7_motion_arbiter"

    def __init__(
        self,
        *,
        robot_service: RobotService,
        settings: Settings,
        config: CompanionDemoConfig | None = None,
        runtime_factory: Callable[[], RuntimeContract] | None = None,
        active_task_provider: Callable[[], object | None] | None = None,
    ) -> None:
        self.robot_service = robot_service
        self.settings = settings
        self.config = config or load_companion_demo_config(
            _resolve_config_path(settings.companion_config_path)
        )
        self._runtime_factory = runtime_factory or self._build_runtime
        self._active_task_provider = active_task_provider or (lambda: None)
        self._runtime: RuntimeContract | None = None
        self._inputs_started = False
        self._control_owned = False
        self._service_state = CompanionServiceState.IDLE
        self._reason = "initialized_idle"
        self._lock = threading.RLock()
        self._generation = 0
        self._state_path = _resolve_runtime_path(settings.companion_state_path)
        self._restart_interrupted = (
            settings.mode == "real" and self._read_persisted_state() != "IDLE"
        )

    def initialize(self) -> None:
        """Service startup is always stationary and never restores FOLLOWING."""

        with self._lock:
            runtime = self._runtime
            if runtime is not None:
                try:
                    runtime.close()
                finally:
                    self._runtime = None
                    self._inputs_started = False
                    self._release_control()
            else:
                self._release_control()
            if self.settings.mode == "real":
                self.robot_service.safe_stop("companion:service_startup_idle")
            self._runtime = None
            self._service_state = CompanionServiceState.IDLE
            self._reason = (
                "service_restart_interrupted"
                if self._restart_interrupted
                else "service_restart_idle"
            )

    def prepare(self) -> dict[str, object]:
        """Start the persistent input runtime without authorizing motion."""

        with self._lock:
            if self._runtime is not None:
                return self.status()
            try:
                runtime = self._runtime_factory()
            except CompanionLifecycleError:
                raise
            except Exception as exc:
                raise CompanionLifecycleError(
                    "COMPANION_RUNTIME_FAILED", str(exc), 500
                ) from exc
            self._runtime = runtime
            self._reason = "preparing_inputs"
        try:
            runtime.start_inputs()
            with self._lock:
                self._inputs_started = True
                self._service_state = CompanionServiceState.IDLE
                self._reason = "inputs_ready_idle"
                return self.status()
        except Exception:
            try:
                runtime.close()
            finally:
                with self._lock:
                    if self._runtime is runtime:
                        self._runtime = None
                        self._inputs_started = False
                        self._service_state = CompanionServiceState.IDLE
                        self._reason = "prepare_failed"
            raise

    def status(self) -> dict[str, object]:
        with self._lock:
            runtime = self._runtime
            if runtime is not None:
                payload = runtime.snapshot().to_dict()
                if self._service_state is CompanionServiceState.STARTING:
                    payload["state"] = CompanionServiceState.STARTING.value
                    payload["reason"] = self._reason
                    payload["runtime_active"] = False
                payload["configuration"] = self._configuration_status()
                return payload
            payload = CompanionStatus(
                state=self._service_state.value,
                reason=self._reason,
                incident_id=None,
                resume_required=self._restart_interrupted,
                runtime_active=False,
                robot_online=self._robot_online(),
            ).to_dict()
            payload["configuration"] = self._configuration_status()
            return payload

    def start(self) -> dict[str, object]:
        with self._lock:
            if self._restart_interrupted:
                raise CompanionLifecycleError(
                    "SERVICE_RESTART_INTERRUPTED",
                    "previous companion runtime did not stop cleanly; issue STOP before START",
                    409,
                )
            if self._runtime is not None:
                if self._service_state is CompanionServiceState.STARTING:
                    return self.status()
                state = self._runtime.state()
                if state == "FOLLOWING":
                    return self._runtime.snapshot().to_dict()
                if state != "IDLE":
                    raise CompanionLifecycleError(
                        "COMPANION_STATE_CONFLICT",
                        f"START is not allowed while companion state is {state}",
                        409,
                    )

            self._validate_real_motion_gates()
            self._ensure_control_available()
            self._service_state = CompanionServiceState.STARTING
            self._reason = "preflight"
            runtime = self._runtime
            if runtime is None:
                try:
                    runtime = self._runtime_factory()
                except CompanionLifecycleError:
                    self._service_state = CompanionServiceState.IDLE
                    self._reason = "start_failed"
                    raise
                except Exception as exc:
                    self._service_state = CompanionServiceState.IDLE
                    self._reason = "start_failed"
                    raise CompanionLifecycleError(
                        "COMPANION_RUNTIME_FAILED", str(exc), 500
                    ) from exc
                self._runtime = runtime
            self._generation += 1
            generation = self._generation

        try:
            if not self._inputs_started:
                runtime.start_inputs()
                with self._lock:
                    self._inputs_started = True
            runtime.wait_until_ready(
                self.settings.companion_startup_timeout_seconds
            )
            self._ensure_control_available()
            with self._lock:
                if self._runtime is not runtime or self._generation != generation:
                    raise CompanionLifecycleError(
                        "COMPANION_STATE_CONFLICT",
                        "companion START was cancelled by STOP",
                        409,
                    )
                self._acquire_control()
                self._ensure_control_available()
                self._persist_state("ACTIVE")
                runtime.activate()
                self._service_state = CompanionServiceState.IDLE
                self._reason = "companion_started"
                return runtime.snapshot().to_dict()
        except Exception as exc:
            with self._lock:
                owns_runtime = self._runtime is runtime
                cancelled = self._generation != generation
                if owns_runtime and not cancelled:
                    self._service_state = CompanionServiceState.IDLE
                    self._reason = "start_failed"
                    self._persist_state("IDLE")
            if owns_runtime and not cancelled:
                if runtime is not None:
                    try:
                        runtime.stop(reason="start_failed")
                    except Exception:
                        self.robot_service.safe_stop("companion:start_cleanup_failed")
                self._release_control()
            if isinstance(exc, CompanionLifecycleError):
                raise
            if isinstance(exc, GatewayError):
                raise CompanionLifecycleError(
                    exc.code.value, exc.message, exc.http_status
                ) from exc
            raise CompanionLifecycleError(
                "COMPANION_RUNTIME_FAILED", str(exc), 500
            ) from exc

    def stop(self) -> dict[str, object]:
        with self._lock:
            self._generation += 1
            runtime = self._runtime
            if runtime is None:
                self.robot_service.safe_stop("companion:stop_idempotent")
                self._release_control()
                self._restart_interrupted = False
                self._persist_state("IDLE")
                self._service_state = CompanionServiceState.IDLE
                self._reason = "already_idle"
                return self.status()
            if (
                runtime.state() == "IDLE"
                and self._service_state is not CompanionServiceState.STARTING
            ):
                self.robot_service.safe_stop("companion:stop_idempotent")
                self._release_control()
                self._restart_interrupted = False
                self._persist_state("IDLE")
                self._service_state = CompanionServiceState.IDLE
                self._reason = "already_idle"
                return self.status()
            try:
                self.robot_service.safe_stop("companion:api_stop_immediate")
                runtime.stop(reason="api_stop")
            finally:
                self._release_control()
                self._service_state = CompanionServiceState.IDLE
                self._reason = "companion_stopped"
                self._restart_interrupted = False
                self._persist_state("IDLE")
            return self.status()

    def resume(self) -> dict[str, object]:
        with self._lock:
            runtime = self._runtime
            if runtime is None or runtime.state() != "WAIT_RESUME":
                state = "IDLE" if runtime is None else runtime.state()
                raise CompanionLifecycleError(
                    "COMPANION_STATE_CONFLICT",
                    f"RESUME is only allowed from WAIT_RESUME, current={state}",
                    409,
                )
            self._ensure_control_available()
            acquired_here = not self._control_owned
            try:
                self._acquire_control()
                self._persist_state("ACTIVE")
                runtime.resume()
                return runtime.snapshot().to_dict()
            except Exception:
                if acquired_here:
                    self._release_control()
                    self._persist_state("IDLE")
                raise

    def close(self) -> None:
        with self._lock:
            self._generation += 1
            runtime = self._runtime
            self._runtime = None
            self._inputs_started = False
        try:
            self.robot_service.safe_stop("companion:service_close_immediate")
            if runtime is not None:
                runtime.close()
        except Exception:
            self.robot_service.safe_stop("companion:service_close_failed")
        finally:
            with self._lock:
                self._release_control()
                self._service_state = CompanionServiceState.IDLE
                self._reason = "service_closed"
                self._restart_interrupted = False
                self._persist_state("IDLE")

    def _build_runtime(self) -> CompanionRuntime:
        return CompanionRuntime(
            robot_service=self.robot_service,
            settings=self.settings,
            config=self.config,
        )

    def _ensure_control_available(self) -> None:
        if self._active_task_provider() is not None:
            raise CompanionLifecycleError(
                "CONTROL_BUSY", "another robot task is active", 409
            )
        try:
            self.robot_service.ensure_ready_for_task_acceptance(
                source=self._CONTROL_OWNER
            )
        except GatewayError as exc:
            raise CompanionLifecycleError(
                exc.code.value, exc.message, exc.http_status
            ) from exc

    def _acquire_control(self) -> None:
        try:
            self.robot_service.acquire_exclusive_control(self._CONTROL_OWNER)
        except GatewayError as exc:
            raise CompanionLifecycleError(
                exc.code.value, exc.message, exc.http_status
            ) from exc
        self._control_owned = True

    def _release_control(self) -> None:
        if not self._control_owned:
            return
        self.robot_service.release_exclusive_control(self._CONTROL_OWNER)
        self._control_owned = False

    def _validate_real_motion_gates(self) -> None:
        if self.settings.mode != "real":
            return
        failures: list[str] = []
        if not self.settings.control_enabled:
            failures.append("GO2_CONTROL_ENABLED must be true")
        if self.settings.read_only_mode:
            failures.append("GO2_READ_ONLY_MODE must be false")
        if self.settings.follow_simulation:
            failures.append("FOLLOW_SIMULATION must be false")
        if not self.settings.follow_execution_enabled:
            failures.append("FOLLOW_EXECUTION_ENABLED must be true")
        if not self.settings.phase7_motion_execution_enabled:
            failures.append("PHASE7_MOTION_EXECUTION_ENABLED must be true")
        if (
            self.settings.phase7_require_external_risk_feed
            and not self.settings.companion_risk_events_path.strip()
        ):
            failures.append("GO2_COMPANION_RISK_EVENTS_PATH is required")
        if self.config.follow.vx_max > self.settings.max_vx:
            failures.append(
                "follow.vx_max_mps exceeds GO2_MAX_VX"
            )
        if self.config.follow.wz_max > self.settings.max_wz:
            failures.append(
                "follow.wz_max_radps exceeds GO2_MAX_WZ"
            )
        if failures:
            raise CompanionLifecycleError(
                "CONTROL_DISABLED", "; ".join(failures), 403
            )

    def _configuration_status(self) -> dict[str, object]:
        profile = self.config.follow
        risk_attached = bool(self.settings.companion_risk_events_path.strip())
        return {
            "target_distance_m": profile.target_distance,
            "target_bearing_rad": profile.target_bearing_radians,
            "control_frequency_hz": min(5.0, profile.control_frequency_hz),
            "motion_limits_aligned": (
                profile.vx_max <= self.settings.max_vx
                and profile.wz_max <= self.settings.max_wz
            ),
            "vx_max_mps": profile.vx_max,
            "gateway_max_vx_mps": self.settings.max_vx,
            "walk_min_mps": profile.walk_min,
            "wz_max_radps": profile.wz_max,
            "gateway_max_wz_radps": self.settings.max_wz,
            "vy_mps": 0.0,
            "risk_feed_mode": (
                "MOCK"
                if self.settings.mode == "mock"
                else ("ACTIVE" if risk_attached else "DISABLED")
            ),
            "fall_preemption_available": (
                self.settings.mode == "mock" or risk_attached
            ),
        }

    def _robot_online(self) -> bool:
        try:
            return bool(self.robot_service.status().get("online"))
        except Exception:
            return False

    def _read_persisted_state(self) -> str:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return "IDLE"
        if not isinstance(payload, dict):
            return "IDLE"
        return str(payload.get("state") or "IDLE").upper()

    def _persist_state(self, state: str) -> None:
        if self.settings.mode != "real":
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_name(
            f".{self._state_path.name}.{os.getpid()}.tmp"
        )
        payload = {"state": state, "automatic_resume": False}
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self._state_path)


def _resolve_config_path(value: str) -> Path:
    configured = Path(value).expanduser()
    if configured.is_absolute() or configured.is_file():
        return configured
    project_relative = Path(__file__).resolve().parents[2] / configured
    return project_relative


def _resolve_runtime_path(value: str) -> Path:
    configured = Path(value).expanduser()
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parents[2] / configured
