const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ts = require("typescript");

const policyPath = path.resolve(__dirname, "../src/utils/companionLifecyclePolicy.ts");
const source = fs.readFileSync(policyPath, "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2020,
    strict: true,
  },
  fileName: policyPath,
});
const moduleUnderTest = { exports: {} };
new Function("exports", "module", "require", compiled.outputText)(
  moduleUnderTest.exports,
  moduleUnderTest,
  require,
);

const {
  companionDistance,
  companionErrorCheckKey,
  companionErrorMessage,
  companionIsMoving,
  companionStateLabel,
} = moduleUnderTest.exports;

assert.equal(companionStateLabel("FOLLOWING"), "正在陪伴");
assert.equal(companionStateLabel("IDLE"), "待机");
assert.equal(
  companionErrorMessage("UWB_NOT_READY", "fallback"),
  "未检测到陪伴目标，请确认老人已携带伴随遥控器。",
);
assert.equal(companionErrorCheckKey("UWB_STALE"), "uwb");
assert.equal(companionErrorCheckKey("LIDAR_STOP_ACTIVE"), "lidar");
assert.equal(companionErrorCheckKey("CONTROL_BUSY"), "control_idle");
assert.equal(companionIsMoving({ motion: { vx: 0.12, vy: 0, wz: 0 }, uwb: {} }), true);
assert.equal(companionIsMoving({ motion: { vx: 0, vy: 0, wz: 0 }, uwb: {} }), false);
assert.equal(companionDistance({ motion: {}, uwb: { distance_m: 1.823 } }), "1.82 m");

const clientSource = fs.readFileSync(path.resolve(__dirname, "../src/api/client.ts"), "utf8");
assert.match(clientSource, /\/elders\/\$\{encodeURIComponent\(elderId\)\}\/robot-companion\/start/);
assert.match(clientSource, /\/elders\/\$\{encodeURIComponent\(elderId\)\}\/robot-companion\/stop/);

const controlSource = fs.readFileSync(
  path.resolve(__dirname, "../src/components/robot/CompanionLifecycleControl.vue"),
  "utf8",
);
assert.match(controlSource, /result\.state !== "FOLLOWING"/);
assert.match(controlSource, /result\.state !== "IDLE"/);
assert.doesNotMatch(controlSource, /fetch\([^\n]*8090/);

console.log("COMPANION_LIFECYCLE_CONTROL_TESTS_OK");
