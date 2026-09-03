import type { RobotReadonlyTelemetryIntegration } from "../types/robot";
import { RobotContractError, validateApiEnvelope } from "./robotContractPolicy";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function validateReadonlyTelemetryEnvelope(
  payload: unknown,
  endpoint: string,
) {
  const envelope = validateApiEnvelope<RobotReadonlyTelemetryIntegration>(payload, endpoint);
  const data = envelope.data;
  if (!isRecord(data)) {
    throw new RobotContractError(
      "ROBOT_API_INVALID_ENVELOPE",
      endpoint,
      "只读遥测响应结构无效",
    );
  }
  if (data.real_motion_enabled !== false) {
    throw new RobotContractError(
      "ROBOT_INTERFACE_SAFETY_CONTRACT_VIOLATION",
      endpoint,
      "只读遥测禁止启用真实运动",
    );
  }
  if (data.provider !== "mock" && data.provider !== "unitree_readonly") {
    throw new RobotContractError(
      "ROBOT_INTERFACE_SAFETY_CONTRACT_VIOLATION",
      endpoint,
      "只读遥测 provider 不在允许列表",
    );
  }
  if (data.provider !== data.integration_mode) {
    throw new RobotContractError(
      "ROBOT_INTERFACE_SAFETY_CONTRACT_VIOLATION",
      endpoint,
      "只读遥测 provider 与 integration_mode 不一致",
    );
  }
  if (data.provider === "unitree_readonly" && data.source_status === "ready") {
    const readonly = data.readonly_status;
    if (
      !isRecord(readonly)
      || readonly.provider !== "unitree_readonly"
      || readonly.real_motion_enabled !== false
      || !isRecord(readonly.motion)
      || readonly.motion.enabled !== false
      || !Array.isArray(readonly.motion.commands_supported)
      || readonly.motion.commands_supported.length !== 0
      || !isRecord(readonly.navigation)
      || readonly.navigation.available !== false
      || !isRecord(readonly.localization)
      || readonly.localization.available !== false
    ) {
      throw new RobotContractError(
        "ROBOT_INTERFACE_SAFETY_CONTRACT_VIOLATION",
        endpoint,
        "Unitree 只读快照违反运动、定位或导航安全边界",
      );
    }
  }
  if (data.provider === "mock" && data.readonly_status !== null) {
    throw new RobotContractError(
      "ROBOT_INTERFACE_SAFETY_CONTRACT_VIOLATION",
      endpoint,
      "Mock 模式不能携带 Unitree 真实快照",
    );
  }
  return envelope;
}
