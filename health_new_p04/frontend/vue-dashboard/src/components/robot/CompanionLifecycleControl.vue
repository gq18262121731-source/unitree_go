<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  Bot,
  Check,
  CircleAlert,
  CircleDot,
  LoaderCircle,
  Play,
  RefreshCw,
  ShieldCheck,
  Square,
} from "lucide-vue-next";
import {
  ApiError,
  api,
  type CareDirectory,
  type ElderCompanionStatus,
} from "../../api/client";
import { getStoredSessionToken } from "../../composables/useSessionAuth";
import {
  companionDistance,
  companionErrorCheckKey,
  companionErrorMessage,
  companionIsMoving,
  companionStateLabel,
} from "../../utils/companionLifecyclePolicy";

const directory = ref<CareDirectory | null>(null);
const selectedElderId = ref("");
const status = ref<ElderCompanionStatus | null>(null);
const loading = ref(true);
const refreshing = ref(false);
const starting = ref(false);
const stopping = ref(false);
const errorCode = ref("");
const errorMessage = ref("");
const operationMessage = ref("");
let pollTimer: number | null = null;
let requestSequence = 0;

const selectedElder = computed(() =>
  directory.value?.elders.find((elder) => elder.id === selectedElderId.value) ?? null,
);
const stateLabel = computed(() => companionStateLabel(status.value?.state ?? "IDLE"));
const isMoving = computed(() => companionIsMoving(status.value));
const distanceLabel = computed(() => companionDistance(status.value));
const displayChecks = computed(() => {
  const failedKey = companionErrorCheckKey(errorCode.value);
  return (status.value?.checks ?? []).map((check) => (
    failedKey && check.key === failedKey ? { ...check, state: "failed" as const, code: errorCode.value } : check
  ));
});
const canStart = computed(() => Boolean(status.value?.can_start && !starting.value && !stopping.value));
const canStop = computed(() => Boolean(status.value?.can_stop && !stopping.value));
const lidarLabel = computed(() => {
  if (!status.value?.runtime_active) return "待 START 检查";
  return status.value.lidar.valid && status.value.lidar.state !== "STOP" ? "安全" : "需要检查";
});
const uwbLabel = computed(() => {
  if (!status.value?.runtime_active) return "待 START 检查";
  return status.value.uwb.valid ? "已连接" : "不可用";
});
const statusTone = computed(() => {
  if (["FOLLOWING", "PERSON_STOPPED", "HOLD"].includes(status.value?.state ?? "")) return "active";
  if (["SAFE_STOP", "EMERGENCY_STOP", "OBSTACLE_STOP", "WAIT_RESUME"].includes(status.value?.state ?? "")) return "warning";
  return "idle";
});

onMounted(async () => {
  try {
    directory.value = await api.getCareDirectory();
    selectedElderId.value = directory.value.elders[0]?.id ?? "";
    await refreshStatus();
  } catch (error) {
    setError(error, "陪伴控制信息加载失败");
  } finally {
    loading.value = false;
  }
  pollTimer = window.setInterval(() => void refreshStatus(true), 1500);
});

onBeforeUnmount(() => {
  if (pollTimer !== null) window.clearInterval(pollTimer);
});

watch(selectedElderId, () => {
  status.value = null;
  errorCode.value = "";
  errorMessage.value = "";
  operationMessage.value = "";
  if (selectedElderId.value) void refreshStatus();
});

async function refreshStatus(quiet = false) {
  const elderId = selectedElderId.value;
  if (!elderId) return;
  const sequence = ++requestSequence;
  if (!quiet) refreshing.value = true;
  try {
    const result = await api.getElderCompanionStatus(elderId, getStoredSessionToken());
    if (sequence === requestSequence && elderId === selectedElderId.value) {
      status.value = result;
      if (!quiet) {
        errorCode.value = "";
        errorMessage.value = "";
      }
    }
  } catch (error) {
    if (!quiet) setError(error, "机器人状态读取失败");
  } finally {
    if (!quiet) refreshing.value = false;
  }
}

async function startCompanion() {
  if (!canStart.value || !selectedElderId.value) return;
  starting.value = true;
  errorCode.value = "";
  errorMessage.value = "";
  operationMessage.value = "正在检查机器人状态…";
  try {
    const result = await api.startElderCompanion(selectedElderId.value, getStoredSessionToken());
    status.value = result;
    if (result.state !== "FOLLOWING") {
      throw new Error(`Gateway 未确认 FOLLOWING，当前为 ${result.state}`);
    }
    operationMessage.value = "伴随模式已启动";
  } catch (error) {
    operationMessage.value = "";
    setError(error, "暂时无法开始陪伴");
    await refreshStatus(true);
  } finally {
    starting.value = false;
  }
}

async function stopCompanion() {
  if (!canStop.value || !selectedElderId.value) return;
  stopping.value = true;
  errorCode.value = "";
  errorMessage.value = "";
  operationMessage.value = "正在停止机器人…";
  try {
    const result = await api.stopElderCompanion(selectedElderId.value, getStoredSessionToken());
    status.value = result;
    if (result.state !== "IDLE") {
      throw new Error(`Gateway 未确认 IDLE，当前为 ${result.state}`);
    }
    operationMessage.value = "机器人已停止";
  } catch (error) {
    operationMessage.value = "";
    setError(error, "停止请求未确认，请立即检查机器人");
  } finally {
    stopping.value = false;
    await refreshStatus(true);
  }
}

function setError(error: unknown, fallback: string) {
  const code = error instanceof ApiError ? error.code : "";
  const detail = error instanceof ApiError
    ? error.detail
    : error instanceof Error
      ? error.message
      : fallback;
  errorCode.value = code || "";
  errorMessage.value = companionErrorMessage(code, detail || fallback);
}
</script>

<template>
  <article class="companion-control" aria-labelledby="companion-control-title">
    <header class="companion-control__header">
      <div class="companion-control__identity">
        <span><Bot :size="25" /></span>
        <div>
          <p>机器人陪伴详情</p>
          <h2 id="companion-control-title">
            {{ selectedElder?.name ?? "请选择老人" }} · {{ status?.robot.name ?? "小康01" }}
          </h2>
          <small>{{ status?.robot.model ?? "Go2 EDU" }}</small>
        </div>
      </div>
      <label class="companion-control__elder">
        <span>监护对象</span>
        <select v-model="selectedElderId" :disabled="loading || starting || stopping">
          <option v-for="elder in directory?.elders" :key="elder.id" :value="elder.id">
            {{ elder.name }} · {{ elder.apartment }}
          </option>
        </select>
      </label>
      <div class="companion-control__state" :class="`is-${statusTone}`" role="status" aria-live="polite">
        <CircleDot :size="17" />
        <span><small>当前状态</small><strong>{{ stateLabel }}</strong></span>
      </div>
    </header>

    <div v-if="loading" class="companion-control__loading">
      <LoaderCircle :size="19" /> 正在读取 Companion 状态…
    </div>

    <template v-else>
      <div class="companion-control__metrics">
        <div><small>绑定 Go2</small><strong>{{ status?.binding.matched ? "已确认" : "未确认" }}</strong></div>
        <div><small>UWB 目标</small><strong>{{ uwbLabel }}</strong></div>
        <div><small>与老人距离</small><strong>{{ distanceLabel }}</strong></div>
        <div><small>周围环境</small><strong>{{ lidarLabel }}</strong></div>
        <div><small>机器人控制</small><strong>{{ status?.gateway_available ? "可用" : "不可用" }}</strong></div>
        <div><small>机器狗</small><strong>{{ isMoving ? "正在移动" : "已停稳" }}</strong></div>
      </div>

      <div class="companion-control__checks">
        <div class="companion-control__checks-title">
          <span><ShieldCheck :size="17" /> 启动检查</span>
          <button type="button" :disabled="refreshing" @click="refreshStatus()">
            <LoaderCircle v-if="refreshing" :size="14" />
            <RefreshCw v-else :size="14" />
            刷新
          </button>
        </div>
        <ul>
          <li v-for="check in displayChecks" :key="check.key" :class="`is-${check.state}`">
            <Check v-if="check.state === 'passed'" :size="15" />
            <CircleAlert v-else-if="check.state === 'failed'" :size="15" />
            <CircleDot v-else :size="15" />
            <span>{{ check.label }}</span>
            <small>{{ check.state === "pending" ? "START 时检查" : check.state === "passed" ? "通过" : check.code }}</small>
          </li>
        </ul>
      </div>

      <div v-if="errorMessage" class="companion-control__error" role="alert">
        <CircleAlert :size="18" />
        <div><strong>暂时无法完成操作</strong><p>{{ errorMessage }}</p></div>
      </div>
      <p v-else-if="operationMessage" class="companion-control__success" role="status">
        <Check :size="17" /> {{ operationMessage }}
      </p>

      <div class="companion-control__actions">
        <button type="button" class="is-start" :disabled="!canStart" @click="startCompanion">
          <LoaderCircle v-if="starting" :size="18" />
          <Play v-else :size="18" fill="currentColor" />
          <span><strong>{{ starting ? "正在检查机器人状态…" : "开始伴随" }}</strong><small>由 health_new 代理到 go2-gateway</small></span>
        </button>
        <button type="button" class="is-stop" :disabled="!canStop" @click="stopCompanion">
          <LoaderCircle v-if="stopping" :size="18" />
          <Square v-else :size="18" fill="currentColor" />
          <span><strong>{{ stopping ? "正在停止…" : "停止伴随" }}</strong><small>重复请求安全，最终必须回到 IDLE</small></span>
        </button>
      </div>
    </template>
  </article>
</template>

<style scoped>
.companion-control { padding: 20px; border: 1px solid #cfdde8; border-radius: 20px; background: #fff; box-shadow: 0 12px 34px rgba(24, 66, 96, .08); }
.companion-control__header { display: grid; grid-template-columns: minmax(240px, 1fr) minmax(220px, .7fr) auto; align-items: center; gap: 18px; }
.companion-control__identity { display: flex; align-items: center; gap: 12px; }
.companion-control__identity > span { width: 48px; height: 48px; display: grid; place-items: center; border-radius: 14px; background: #e9f4ff; color: #1765c2; }
.companion-control__identity p, .companion-control__identity h2 { margin: 0; }
.companion-control__identity p { color: #648096; font-size: .68rem; font-weight: 800; letter-spacing: .08em; }
.companion-control__identity h2 { margin-top: 3px; color: #102a43; font-size: 1.05rem; }
.companion-control__identity small { color: #6d8192; }
.companion-control__elder { display: grid; gap: 5px; color: #5d7588; font-size: .7rem; font-weight: 750; }
.companion-control__elder select { min-height: 40px; padding: 0 11px; border: 1px solid #cbd8e4; border-radius: 9px; background: #f8fbfd; color: #173f5f; }
.companion-control__state { min-width: 130px; display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-radius: 11px; background: #f1f5f8; color: #587084; }
.companion-control__state.is-active { background: #eaf8f2; color: #087653; }
.companion-control__state.is-warning { background: #fff3e8; color: #a45a0a; }
.companion-control__state span { display: grid; gap: 1px; }
.companion-control__state small { font-size: .64rem; }
.companion-control__state strong { font-size: .83rem; }
.companion-control__loading { min-height: 170px; display: flex; align-items: center; justify-content: center; gap: 8px; color: #607b91; }
.companion-control__loading svg, button svg.lucide-loader-circle { animation: companion-spin 1s linear infinite; }
.companion-control__metrics { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 10px; margin-top: 18px; }
.companion-control__metrics > div { min-width: 0; display: grid; gap: 4px; padding: 11px 12px; border-radius: 10px; background: #f4f8fb; }
.companion-control__metrics small { color: #71869a; font-size: .66rem; }
.companion-control__metrics strong { overflow: hidden; color: #173f5f; font-size: .8rem; text-overflow: ellipsis; white-space: nowrap; }
.companion-control__checks { margin-top: 14px; padding: 13px 14px; border: 1px solid #dbe5ed; border-radius: 12px; }
.companion-control__checks-title { display: flex; justify-content: space-between; align-items: center; }
.companion-control__checks-title > span { display: flex; align-items: center; gap: 7px; color: #294f6c; font-size: .78rem; font-weight: 800; }
.companion-control__checks-title button { display: flex; align-items: center; gap: 5px; border: 0; background: transparent; color: #376d94; font-size: .72rem; cursor: pointer; }
.companion-control__checks ul { margin: 12px 0 0; padding: 0; display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; list-style: none; }
.companion-control__checks li { min-width: 0; display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 2px 6px; align-items: center; padding: 8px 9px; border-radius: 8px; background: #f4f7f9; color: #63798a; }
.companion-control__checks li.is-passed { background: #edf8f3; color: #087653; }
.companion-control__checks li.is-failed { background: #fff2f0; color: #ad3f37; }
.companion-control__checks li span { overflow: hidden; font-size: .7rem; font-weight: 750; text-overflow: ellipsis; white-space: nowrap; }
.companion-control__checks li small { grid-column: 2; overflow: hidden; font-size: .61rem; text-overflow: ellipsis; white-space: nowrap; }
.companion-control__error, .companion-control__success { display: flex; gap: 9px; align-items: flex-start; margin: 13px 0 0; padding: 11px 13px; border-radius: 10px; }
.companion-control__error { background: #fff2f0; color: #a53a33; }
.companion-control__error p { margin: 3px 0 0; font-size: .75rem; }
.companion-control__success { align-items: center; background: #edf8f3; color: #087653; font-size: .78rem; font-weight: 750; }
.companion-control__actions { display: grid; grid-template-columns: 1fr 1fr; gap: 11px; margin-top: 14px; }
.companion-control__actions button { display: grid; grid-template-columns: auto 1fr; align-items: center; gap: 11px; padding: 13px 14px; border: 1px solid; border-radius: 11px; font: inherit; text-align: left; cursor: pointer; }
.companion-control__actions button span { display: grid; gap: 2px; }
.companion-control__actions button small { font-size: .68rem; opacity: .78; }
.companion-control__actions .is-start { border-color: #1765c2; background: #174ea6; color: #fff; }
.companion-control__actions .is-stop { border-color: #e2aaa4; background: #fff4f2; color: #a43a34; }
.companion-control__actions button:disabled { opacity: .42; cursor: not-allowed; }
@keyframes companion-spin { to { transform: rotate(360deg); } }
@media (max-width: 1000px) { .companion-control__header { grid-template-columns: 1fr 1fr; } .companion-control__state { grid-column: 1 / -1; } .companion-control__metrics { grid-template-columns: repeat(3, 1fr); } .companion-control__checks ul { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 620px) { .companion-control__header, .companion-control__actions { grid-template-columns: 1fr; } .companion-control__metrics { grid-template-columns: repeat(2, 1fr); } .companion-control__checks ul { grid-template-columns: 1fr; } }
</style>
