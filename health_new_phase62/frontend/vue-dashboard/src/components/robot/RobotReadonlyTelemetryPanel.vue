<script setup lang="ts">
import { computed } from "vue";
import { BatteryCharging, Bot, Radio, ShieldCheck } from "lucide-vue-next";
import type {
  RobotReadonlySensorObservation,
  RobotReadonlyTelemetryIntegration,
} from "../../types/robot";

const props = defineProps<{
  telemetry: RobotReadonlyTelemetryIntegration;
}>();

const status = computed(() => props.telemetry.readonly_status);
const battery = computed(() => {
  const value = status.value?.robot.telemetry.value.battery_percentage;
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value)}%`
    : "未提供";
});

const sensors = computed(() => {
  const value = status.value?.sensors;
  return [
    { label: "L1 LiDAR", sensor: value?.lidar },
    { label: "IMU", sensor: value?.imu },
    { label: "Odometry", sensor: value?.odometry },
  ];
});

function sensorState(sensor: RobotReadonlySensorObservation | undefined) {
  if (!sensor?.available) return "未接入";
  if (!sensor.fresh) return "数据陈旧";
  if (sensor.semantic_valid === false) return "在线 / 语义 HOLD";
  return "在线";
}

function formatHz(value: number | null | undefined) {
  return typeof value === "number" ? `${value.toFixed(1)} Hz` : "—";
}
</script>

<template>
  <article class="readonly-panel" :class="{ 'is-unavailable': telemetry.source_status !== 'ready' }">
    <header>
      <span class="readonly-panel__icon"><ShieldCheck :size="22" /></span>
      <div>
        <p>UNITREE READONLY TELEMETRY</p>
        <h2>Unitree 真实只读环境</h2>
      </div>
      <code>real_motion_enabled=false</code>
    </header>

    <div v-if="status" class="readonly-panel__facts">
      <div>
        <Bot :size="17" />
        <span>机器人</span>
        <strong>{{ status.robot.online ? "在线" : "离线" }}</strong>
        <small>{{ status.robot.model }} · {{ status.robot.firmware }}</small>
      </div>
      <div>
        <BatteryCharging :size="17" />
        <span>电量</span>
        <strong>{{ battery }}</strong>
        <small>仅显示适配器实际提供值</small>
      </div>
      <div>
        <Radio :size="17" />
        <span>DDS / ROS2</span>
        <strong>{{ status.transport.healthy ? "健康" : "异常" }}</strong>
        <small>{{ status.transport.source ?? "来源未提供" }}</small>
      </div>
    </div>

    <div v-if="status" class="readonly-panel__sensors">
      <div v-for="item in sensors" :key="item.label">
        <span>{{ item.label }}</span>
        <strong>{{ sensorState(item.sensor) }}</strong>
        <small>{{ formatHz(item.sensor?.frequency_hz) }}</small>
      </div>
    </div>

    <div v-if="status" class="readonly-panel__holds">
      <span>定位：未接入</span>
      <span>导航：未接入</span>
      <span>运动控制：关闭</span>
      <span>健康态：{{ status.health.status }}</span>
    </div>

    <div v-else class="readonly-panel__error" role="status">
      <strong>只读数据源暂不可用</strong>
      <p>{{ telemetry.error_message ?? "尚未配置或快照未通过 Phase 6.1 契约校验。" }}</p>
      <code>{{ telemetry.error_code ?? telemetry.source_status }}</code>
    </div>
  </article>
</template>

<style scoped>
.readonly-panel { display: grid; gap: 14px; padding: 16px; border: 1px solid #9fd5c1; border-left: 4px solid #0b8f68; border-radius: 13px; background: #edf9f4; color: #155c49; }
.readonly-panel.is-unavailable { border-color: #edca8f; border-left-color: #b8780a; background: #fff8e8; color: #815609; }
.readonly-panel header { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: center; gap: 12px; }
.readonly-panel__icon { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 11px; background: rgba(11, 143, 104, 0.11); }
.readonly-panel header p { margin: 0; font-size: 0.62rem; font-weight: 850; letter-spacing: 0.08em; }
.readonly-panel h2 { margin: 3px 0 0; color: #124f40; font-size: 0.92rem; }
.readonly-panel header code, .readonly-panel__error code { font-family: var(--font-mono); font-size: 0.66rem; }
.readonly-panel__facts, .readonly-panel__sensors { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 9px; }
.readonly-panel__facts > div, .readonly-panel__sensors > div { min-width: 0; display: grid; grid-template-columns: auto 1fr; gap: 3px 8px; padding: 10px; border: 1px solid rgba(40, 120, 94, 0.16); border-radius: 10px; background: rgba(255, 255, 255, 0.65); }
.readonly-panel__facts svg { grid-row: 1 / 3; align-self: center; }
.readonly-panel__facts span, .readonly-panel__sensors span { font-size: 0.65rem; }
.readonly-panel__facts strong, .readonly-panel__sensors strong { color: #133f35; font-size: 0.76rem; }
.readonly-panel__facts small, .readonly-panel__sensors small { grid-column: 2; overflow: hidden; color: #58786f; font-size: 0.62rem; text-overflow: ellipsis; white-space: nowrap; }
.readonly-panel__sensors > div { grid-template-columns: 1fr auto; }
.readonly-panel__sensors small { grid-column: 1 / -1; }
.readonly-panel__holds { display: flex; flex-wrap: wrap; gap: 7px; }
.readonly-panel__holds span { padding: 6px 8px; border-radius: 8px; background: rgba(15, 92, 70, 0.08); font-size: 0.65rem; font-weight: 700; }
.readonly-panel__error strong { font-size: 0.8rem; }
.readonly-panel__error p { margin: 4px 0; font-size: 0.7rem; line-height: 1.45; }
@media (max-width: 760px) {
  .readonly-panel header { grid-template-columns: auto 1fr; }
  .readonly-panel header code { grid-column: 2; }
  .readonly-panel__facts, .readonly-panel__sensors { grid-template-columns: 1fr; }
}
</style>
