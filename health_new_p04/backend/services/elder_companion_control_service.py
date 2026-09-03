from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.config import Settings
from backend.models.elder_companion_model import (
    CompanionBindingStatus,
    CompanionRobotStatus,
    CompanionStartCheck,
    ElderCompanionStatus,
)
from backend.services.care_service import CareService
from backend.services.companion_risk_service import CompanionRiskService
from backend.services.robot_gateway_service import RobotGatewayService


@dataclass
class ElderCompanionControlError(RuntimeError):
    code: str
    message: str
    status_code: int = 409

    def __str__(self) -> str:
        return self.code


class ElderCompanionControlService:
    """Elder-scoped lifecycle proxy; raw robot motion never crosses this boundary."""

    def __init__(
        self,
        *,
        settings: Settings,
        gateway: RobotGatewayService,
        care_service: CareService,
        risk_service: CompanionRiskService,
    ) -> None:
        self._settings = settings
        self._gateway = gateway
        self._care_service = care_service
        self._risk_service = risk_service

    def status(self, elder_id: str) -> ElderCompanionStatus:
        elder = self._require_elder(elder_id)
        companion_result = self._gateway.companion_status()
        robot_result = self._gateway.status()
        return self._build_status(elder, companion_result, robot_result)

    def start(self, elder_id: str) -> ElderCompanionStatus:
        elder = self._require_elder(elder_id)
        self._require_matching_binding(elder.id)
        if self._risk_service.motion_conflict_code() is not None:
            raise ElderCompanionControlError(
                "RISK_LOCK_ACTIVE",
                "存在活动跌倒风险锁，暂时不能开始伴随。",
                409,
            )
        result = self._gateway.start_companion()
        self._raise_gateway_error(result, default_code="COMPANION_START_FAILED")
        status = self._build_status(elder, result, self._gateway.status())
        if status.state != "FOLLOWING":
            raise ElderCompanionControlError(
                "COMPANION_START_NOT_CONFIRMED",
                f"Gateway 未确认 FOLLOWING，当前状态为 {status.state}。",
                409,
            )
        return status

    def stop(self, elder_id: str) -> ElderCompanionStatus:
        elder = self._require_elder(elder_id)
        result = self._gateway.stop_companion()
        self._raise_gateway_error(result, default_code="COMPANION_STOP_FAILED")
        status = self._build_status(elder, result, self._gateway.status())
        if status.state != "IDLE":
            raise ElderCompanionControlError(
                "COMPANION_STOP_NOT_CONFIRMED",
                f"Gateway 未确认 IDLE，当前状态为 {status.state}。",
                502,
            )
        return status

    def _build_status(
        self,
        elder: Any,
        companion_result: dict[str, Any],
        robot_result: dict[str, Any],
    ) -> ElderCompanionStatus:
        companion = self._response_data(companion_result)
        robot_detail = self._response_data(robot_result)
        gateway_available = bool(companion_result.get("ok"))
        binding = self._binding_for(elder.id)
        state = str(companion.get("state") or "IDLE")
        runtime_active = bool(companion.get("runtime_active"))
        robot_online = bool(companion.get("robot_online") or robot_detail.get("online"))
        risk = self._dict(companion.get("risk"))
        health_risk = self._risk_service.status()
        checks = self._checks(
            binding=binding,
            gateway_available=gateway_available,
            state=state,
            runtime_active=runtime_active,
            robot_online=robot_online,
            companion=companion,
            robot_detail=robot_detail,
            health_risk_motion_allowed=health_risk.motion_allowed,
        )
        start_blocking_keys = {
            "binding",
            "gateway",
            "robot_online",
            "dds",
            "risk_clear",
            "manual_takeover",
            "control_idle",
            "speed_contract",
        }
        can_start = state == "IDLE" and all(
            check.state != "failed" for check in checks if check.key in start_blocking_keys
        )
        robot_id = str(robot_detail.get("robotId") or robot_detail.get("robot_id") or self._settings.companion_robot_id)
        return ElderCompanionStatus(
            elder_id=elder.id,
            elder_name=elder.name,
            binding=binding,
            robot=CompanionRobotStatus(
                robot_id=robot_id,
                name=self._settings.companion_robot_name,
                model=self._settings.companion_robot_model,
                online=robot_online,
            ),
            gateway_available=gateway_available,
            state=state,
            reason=str(companion.get("reason") or companion_result.get("error") or "status_unavailable"),
            runtime_active=runtime_active,
            resume_required=bool(companion.get("resume_required")),
            incident_id=companion.get("incident_id"),
            uwb=self._dict(companion.get("uwb")),
            lidar=self._dict(companion.get("lidar")),
            risk={**risk, "health_new_motion_allowed": health_risk.motion_allowed},
            motion=self._dict(companion.get("motion")),
            configuration=self._dict(companion.get("configuration")),
            checks=checks,
            can_start=can_start,
            can_stop=gateway_available,
        )

    def _checks(
        self,
        *,
        binding: CompanionBindingStatus,
        gateway_available: bool,
        state: str,
        runtime_active: bool,
        robot_online: bool,
        companion: dict[str, Any],
        robot_detail: dict[str, Any],
        health_risk_motion_allowed: bool,
    ) -> list[CompanionStartCheck]:
        uwb = self._dict(companion.get("uwb"))
        lidar = self._dict(companion.get("lidar"))
        risk = self._dict(companion.get("risk"))
        configuration = self._dict(companion.get("configuration"))
        dds = self._dict(robot_detail.get("dds"))
        control = self._dict(robot_detail.get("control"))
        inputs_active = runtime_active or state not in {"IDLE", "STARTING"}
        risk_clear = (
            health_risk_motion_allowed
            and not risk.get("incident_id")
            and not risk.get("emergency_active")
        )
        manual_clear = not bool(risk.get("manual_takeover"))
        dds_initialized = dds.get("ddsInitialized", dds.get("dds_initialized"))
        dds_state = dds.get("ddsStateAvailable", dds.get("dds_state_available"))
        dds_passed = robot_online and dds_initialized is not False and dds_state is not False
        speed_aligned = configuration.get("motion_limits_aligned")
        companion_owns_control = state not in {"IDLE", "STARTING", "ERROR"}
        control_available = companion_owns_control or not bool(control.get("busy"))
        return [
            self._check("binding", "老人已绑定 Go2", binding.matched, "COMPANION_BINDING_MISMATCH"),
            self._check("gateway", "go2-gateway 可用", gateway_available, "ROBOT_GATEWAY_UNAVAILABLE"),
            self._check("robot_online", "Go2 在线", robot_online, "ROBOT_OFFLINE"),
            self._check("dds", "DDS 正常", dds_passed, "DDS_NOT_READY"),
            self._input_check("uwb", "UWB 有效", inputs_active, bool(uwb.get("valid")), "UWB_NOT_READY"),
            self._input_check("lidar", "LiDAR 安全", inputs_active, bool(lidar.get("valid")) and lidar.get("state") != "STOP", "LIDAR_NOT_READY"),
            self._check("risk_clear", "无活动跌倒事件", risk_clear, "RISK_LOCK_ACTIVE"),
            self._check("manual_takeover", "无人工接管", manual_clear, "MANUAL_TAKEOVER_ACTIVE"),
            self._check("control_idle", "运动控制空闲", control_available, "CONTROL_BUSY"),
            self._check("speed_contract", "速度配置一致", speed_aligned is not False, "SPEED_LIMIT_MISMATCH"),
        ]

    @staticmethod
    def _check(key: str, label: str, passed: bool, code: str) -> CompanionStartCheck:
        return CompanionStartCheck(
            key=key,
            label=label,
            state="passed" if passed else "failed",
            code=None if passed else code,
        )

    @staticmethod
    def _input_check(key: str, label: str, active: bool, passed: bool, code: str) -> CompanionStartCheck:
        if not active:
            return CompanionStartCheck(
                key=key,
                label=label,
                state="pending",
                code=code,
                detail="点击开始后由 Gateway 在激活运动前检查",
            )
        return ElderCompanionControlService._check(key, label, passed, code)

    def _binding_for(self, elder_id: str) -> CompanionBindingStatus:
        configured_elder = str(
            self._settings.companion_bound_elder_id
            or self._settings.fall_detection_target_elder_id
            or ""
        ).strip()
        return CompanionBindingStatus(
            configured=bool(configured_elder),
            matched=bool(configured_elder and configured_elder == elder_id),
            elder_id=configured_elder or None,
            robot_id=self._settings.companion_robot_id,
        )

    def _require_matching_binding(self, elder_id: str) -> None:
        binding = self._binding_for(elder_id)
        if not binding.configured:
            raise ElderCompanionControlError(
                "COMPANION_BINDING_NOT_CONFIGURED",
                "尚未配置监护对象与 Go2 的绑定关系。",
                409,
            )
        if not binding.matched:
            raise ElderCompanionControlError(
                "COMPANION_BINDING_MISMATCH",
                "当前老人未绑定到这台 Go2。",
                409,
            )

    def _require_elder(self, elder_id: str) -> Any:
        normalized = elder_id.strip()
        elder = next(
            (item for item in self._care_service.get_directory().elders if item.id == normalized),
            None,
        )
        if elder is None:
            raise ElderCompanionControlError("ELDER_NOT_FOUND", "未找到监护对象。", 404)
        return elder

    @staticmethod
    def _raise_gateway_error(result: dict[str, Any], *, default_code: str) -> None:
        if result.get("ok"):
            return
        response = result.get("data") if isinstance(result.get("data"), dict) else {}
        detail = response.get("detail") if isinstance(response.get("detail"), dict) else {}
        code = str(response.get("code") or detail.get("code") or default_code)
        message = str(response.get("message") or detail.get("message") or result.get("error") or code)
        status_code = result.get("status_code")
        if not isinstance(status_code, int) or status_code < 400 or status_code > 599:
            status_code = 503
        raise ElderCompanionControlError(code, message, status_code)

    @staticmethod
    def _response_data(result: dict[str, Any]) -> dict[str, Any]:
        response = result.get("data")
        if not isinstance(response, dict):
            return {}
        nested = response.get("data")
        return dict(nested) if isinstance(nested, dict) else dict(response)

    @staticmethod
    def _dict(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}
