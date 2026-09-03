import type { ElderCompanionStatus } from "../api/client";

const ERROR_MESSAGES: Record<string, string> = {
  COMPANION_BINDING_NOT_CONFIGURED: "尚未绑定监护对象与 Go2，请先完成现场配置。",
  COMPANION_BINDING_MISMATCH: "当前老人未绑定到这台 Go2，请重新选择监护对象。",
  ROBOT_GATEWAY_UNAVAILABLE: "机器人网关暂不可用，请检查 8090 服务。",
  ROBOT_OFFLINE: "Go2 当前离线，请检查电源、网络和 DDS。",
  DDS_NOT_READY: "DDS 状态尚未就绪，请检查机器人网络连接。",
  UWB_NOT_READY: "未检测到陪伴目标，请确认老人已携带伴随遥控器。",
  UWB_STALE: "陪伴目标数据已过期，请检查 UWB 遥控器。",
  LIDAR_NOT_READY: "LiDAR 数据尚未就绪，暂时不能开始陪伴。",
  LIDAR_STOP_ACTIVE: "前方安全距离不足，请清理机器人周围环境。",
  CONTROL_BUSY: "机器人运动控制正被其他任务占用。",
  CONTROL_DISABLED: "真机运动控制尚未启用或速度配置未通过。",
  RISK_LOCK_ACTIVE: "存在活动跌倒事件，暂时不能开始陪伴。",
  FALL_LOCK_ACTIVE: "存在活动跌倒事件，暂时不能开始陪伴。",
  MANUAL_TAKEOVER_ACTIVE: "机器人当前由人工接管，不能开始自动陪伴。",
  RISK_NOT_READY: "风险安全心跳尚未就绪，请检查安全事件链路。",
  SPEED_LIMIT_MISMATCH: "伴随速度与网关安全上限不一致，请先修正配置。",
};

export function companionStateLabel(state: string): string {
  return ({
    IDLE: "待机",
    STARTING: "启动检查中",
    FOLLOWING: "正在陪伴",
    PERSON_STOPPED: "老人已停下",
    HOLD: "保持静止",
    TARGET_LOST: "目标丢失",
    OBSTACLE_STOP: "障碍停车",
    SAFE_STOP: "安全停车",
    WAIT_RESUME: "等待人工继续",
    MONITORING: "事故监护中",
    EMERGENCY_STOP: "紧急停止",
  } as Record<string, string>)[state] ?? state;
}

export function companionErrorMessage(code: string | null | undefined, fallback: string): string {
  return (code && ERROR_MESSAGES[code]) || fallback;
}

export function companionErrorCheckKey(code: string | null | undefined): string {
  if (!code) return "";
  if (["UWB_NOT_READY", "UWB_STALE"].includes(code)) return "uwb";
  if (["LIDAR_NOT_READY", "LIDAR_STOP_ACTIVE"].includes(code)) return "lidar";
  if (["RISK_LOCK_ACTIVE", "FALL_LOCK_ACTIVE", "RISK_NOT_READY"].includes(code)) return "risk_clear";
  if (code === "MANUAL_TAKEOVER_ACTIVE") return "manual_takeover";
  if (code === "CONTROL_BUSY") return "control_idle";
  if (["CONTROL_DISABLED", "SPEED_LIMIT_MISMATCH"].includes(code)) return "speed_contract";
  if (code === "ROBOT_OFFLINE") return "robot_online";
  if (code === "DDS_NOT_READY") return "dds";
  if (code === "ROBOT_GATEWAY_UNAVAILABLE") return "gateway";
  if (["COMPANION_BINDING_NOT_CONFIGURED", "COMPANION_BINDING_MISMATCH"].includes(code)) return "binding";
  return "";
}

export function companionIsMoving(status: ElderCompanionStatus | null): boolean {
  if (!status) return false;
  return [status.motion.vx, status.motion.vy, status.motion.wz].some((value) => {
    const number = Number(value ?? 0);
    return Number.isFinite(number) && Math.abs(number) > 0.01;
  });
}

export function companionDistance(status: ElderCompanionStatus | null): string {
  const value = Number(status?.uwb.distance_m);
  return Number.isFinite(value) ? `${value.toFixed(2)} m` : "待 START 检查";
}
