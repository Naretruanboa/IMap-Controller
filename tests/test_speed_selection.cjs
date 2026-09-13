const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");
const source = readFileSync("static/js/app.js", "utf8");
const start = source.indexOf('      const targetMode = message.speed_schedule');
const end = source.indexOf('      controls();', start);
const update = source.slice(start, end);
function fixture(serverMode, selection) {
  const elements = {
    "#speed-schedule": { value: selection },
    "#target-speed-note": {},
    "#speed-schedule-status": {},
  };
  const context = {
    serverSpeedSchedule: serverMode,
    speedScheduleTimer: null,
    clearTimeout() {},
    $(key) { return elements[key]; },
    cancelSpeedSchedule() { elements["#speed-schedule"].value = "off"; },
  };
  return {
    elements,
    receive(mode, elapsed = 0) {
      context.message = { speed_schedule: mode, speed_schedule_elapsed: elapsed };
      runInNewContext("{\n" + update + "\n}", context);
    },
  };
}
test("idle broadcasts preserve 10 km selection before change is dispatched", () => {
  const f = fixture("off", "target10k");
  f.receive("off");
  f.receive("off");
  assert.equal(f.elements["#speed-schedule"].value, "target10k");
  f.receive("target10k");
  assert.equal(f.elements["#speed-schedule"].value, "target10k");
});
test("active broadcasts allow selecting a different schedule", () => {
  const f = fixture("target10k", "steps");
  f.receive("target10k", 12);
  assert.equal(f.elements["#speed-schedule"].value, "steps");
});
test("server transitions still sync activation and cancellation", () => {
  const f = fixture("off", "off");
  f.receive("target10k");
  assert.equal(f.elements["#speed-schedule"].value, "target10k");
  f.receive("off", 3600);
  assert.equal(f.elements["#speed-schedule"].value, "off");

});

test("target schedule displays continuous elapsed time after an hour", () => {
  const f = fixture("target10k", "target10k");
  f.receive("target10k", 3601);
  assert.equal(f.elements["#speed-schedule"].value, "target10k");
  assert.equal(f.elements["#speed-schedule-status"].textContent, "Auto · 60:01 · Continuous");
});
