const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const read = (relativePath) => fs.readFileSync(path.join(root, relativePath), "utf8");

const routing = read("src/composables/useHashRouting.ts");
const navigation = read("src/components/layout/PrimaryNav.vue");
const app = read("src/App.vue");
const api = read("src/api/robotNavigationApi.ts");
const telemetryPolicy = read("src/api/robotTelemetryPolicy.ts");
const websocket = read("src/composables/useRobotWebSocket.ts");
const statusComposable = read("src/composables/useRobotStatus.ts");
const statusPage = read("src/views/RobotStatusPage.vue");
const telemetryPanel = read("src/components/robot/RobotReadonlyTelemetryPanel.vue");
const robotPresentation = read("src/utils/robotPresentation.ts");
const videoComposable = read("src/composables/useGo2VideoBridge.ts");
const videoPanel = read("src/components/robot/Go2VideoPanel.vue");
const followPage = read("src/views/RobotFollowPage.vue");
const ts = require("typescript");

const policyPath = path.join(root, "src/api/robotContractPolicy.ts");
const policyCompiled = ts.transpileModule(read("src/api/robotContractPolicy.ts"), {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    strict: true,
  },
  fileName: policyPath,
});
const policyModule = { exports: {} };
new Function("exports", "module", "require", policyCompiled.outputText)(
  policyModule.exports,
  policyModule,
  require,
);
const { validateRobotEnvelope, RobotContractError } = policyModule.exports;

const validEnvelope = validateRobotEnvelope({
  success: true,
  code: "OK",
  message: "ok",
  timestamp: "2026-07-23T00:00:00Z",
  data: { provider: "mock", real_motion_enabled: false },
}, "/robot/navigation/state");
assert.equal(validEnvelope.data.provider, "mock");
assert.throws(
  () => validateRobotEnvelope({
    success: true,
    code: "OK",
    message: "ok",
    timestamp: "2026-07-23T00:00:00Z",
    data: { provider: "real", real_motion_enabled: false },
  }, "/robot/navigation/state"),
  (error) => error instanceof RobotContractError
    && error.code === "ROBOT_INTERFACE_SAFETY_CONTRACT_VIOLATION",
);
assert.throws(
  () => validateRobotEnvelope({
    success: true,
    code: "OK",
    message: "ok",
    timestamp: "2026-07-23T00:00:00Z",
    data: { provider: "mock", real_motion_enabled: true },
  }, "/robot/navigation/state"),
  (error) => error instanceof RobotContractError
    && error.code === "ROBOT_INTERFACE_SAFETY_CONTRACT_VIOLATION",
);

assert.match(routing, /"robot-status": "#\/robot-status"/, "Hash route must expose #/robot-status");
assert.match(
  routing,
  /role === "family"\) return \["family"\]/,
  "Family role must remain restricted to the family page",
);
assert.match(
  routing,
  /"robot-tasks", "robot-status", "robot-navigation", "robot-follow"/,
  "Community/admin allow-list must include robot-status",
);
assert.match(navigation, /label: "机器人状态"/, "Primary navigation must include the robot status label");
assert.match(app, /activePage === 'robot-status'/, "App must render RobotStatusPage for robot-status");

for (const endpoint of [
  "/robot/status",
  "/robot/telemetry",
  "/robot/status/diagnostics",
  "/robot/navigation/capabilities",
  "/robot/navigation/state",
]) {
  assert.ok(api.includes(endpoint), `REST client is missing ${endpoint}`);
}
assert.match(api, /requestJson/, "Robot REST client must reuse the existing requestJson helper");
assert.match(api, /validateRobotEnvelope/, "Runtime robot contract validation is required");
assert.doesNotMatch(api, /8090/, "Robot REST client must never address go2-gateway:8090");

assert.match(websocket, /new Map<string, SharedRobotSocket>/, "WebSocket connections must be shared by path");
assert.match(websocket, /sharedSockets\.delete/, "Last subscriber must release the shared connection");
assert.match(websocket, /socket\?\.close\(1000, "page unmounted"\)/, "Unmount must close the WebSocket");
assert.match(websocket, /RETRY_DELAYS_MS/, "Reconnect must use bounded backoff");
assert.match(websocket, /refreshSnapshot/, "Reconnect must refresh the REST snapshot");
assert.doesNotMatch(websocket, /8090\/ws/, "WebSocket client must not hardcode an 8090 connection");

assert.match(statusComposable, /path: "\/ws\/robot\/status"/, "Status page must use one status WebSocket path");
assert.match(statusComposable, /\.slice\(0, 20\)/, "Status timeline must remain bounded at 20 entries");
assert.match(statusComposable, /Promise\.allSettled/, "Four REST cards must degrade independently");
assert.match(statusPage, /MOCK_ENVIRONMENT_NOTICE/, "Status page must render the shared Mock warning");
assert.match(
  robotPresentation,
  /当前为模拟导航环境，真实机器人运动控制已禁用。/,
  "The shared Mock warning must use the frozen competition wording",
);
assert.match(statusPage, /real_motion_enabled=false/, "Real motion disabled invariant must be visible");
assert.match(
  telemetryPolicy,
  /readonly\.motion\.enabled !== false/,
  "Readonly telemetry policy must reject enabled motion",
);
assert.match(
  telemetryPolicy,
  /readonly\.navigation\.available !== false/,
  "Readonly telemetry policy must reject navigation availability",
);
assert.match(
  telemetryPanel,
  /在线 \/ 语义 HOLD/,
  "Readonly telemetry panel must distinguish IMU availability from semantic validity",
);
assert.match(
  telemetryPanel,
  /仅显示适配器实际提供值/,
  "Battery status must not be fabricated",
);
assert.match(statusPage, /暂无当前机器人任务/, "Current-task empty state must be present");

assert.match(videoPanel, /useGo2VideoBridge/, "Video panel must reuse the existing video bridge composable");
assert.match(videoComposable, /onBeforeUnmount/, "Video bridge must clean up on unmount");
assert.match(videoComposable, /clearPollTimer\(\)/, "Video bridge poll timer must be cleared");
assert.match(videoComposable, /activeRequest\?\.abort\(\)/, "Video bridge request must be aborted on unmount");
assert.match(followPage, /function startFollow\(\)/, "RobotFollowPage start behavior must remain present");
assert.match(followPage, /function stopFollow\(\)/, "RobotFollowPage stop behavior must remain present");

const newSources = [
  "src/types/robot.ts",
  "src/api/robotNavigationApi.ts",
  "src/api/robotContractPolicy.ts",
  "src/api/robotTelemetryPolicy.ts",
  "src/composables/useRobotWebSocket.ts",
  "src/composables/useRobotStatus.ts",
  "src/components/robot/RobotReadonlyTelemetryPanel.vue",
  "src/views/RobotStatusPage.vue",
].map(read).join("\n");
assert.doesNotMatch(newSources, /:\s*any\b|as\s+any\b|<any>/, "New robot status sources must not introduce any");

console.log("ROBOT_STATUS_CONTRACT_TESTS_OK");
