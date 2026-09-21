const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { runInNewContext } = require("node:vm");

const source = readFileSync("static/js/app.js", "utf8");
const workflow = source.slice(source.indexOf("async function ensureCatchResultDismissed("),
  source.indexOf("async function runQuickSpinWorkflow("));
const capture = source.slice(source.indexOf("async function captureAndDetectScreen("),
  source.indexOf("async function dismissCatchResult("));

function fixture(states) {
  const calls = [];
  const context = {
    activeCatchWorkflow: false, activeSpinWorkflow: null, lastCatchTimestamp: 0,
    screenLiveTimer: null, screenBusy: false, screenFrameBitmap: null,
    currentScreenBoxes: [], document: { hidden: true },
    Date, console, encodeURIComponent,
    isAutoCatchEnabled: () => true, isAutoSpinEnabled: () => false,
    isAutoPokemonEnabled: () => false,
    isLiveEnabled: () => false,
    $: () => ({ value: "device", checked: true, style: {} }),
    clearTimeout() {}, setTimeout(fn) { fn(); },
    appendSpinLog() {}, throwLog() {}, toast() {},
    recoverStuckPokestop: async () => {},
    recoverStuckPokemonDetail: async () => {},
    fetch: async () => ({ ok: true, blob: async () => ({}) }),
    createImageBitmap: async () => ({ close() {} }),
    detectScreenBoxes: async () => [], renderScreenCanvas: async () => {},
    chooseNextMapTarget: () => null,
    clickDetectedPokemon: async () => false,
    api: async (path, body) => {
      calls.push({ path, body });
      if (path.includes("detect_encounter")) return states.shift() || {};
      return { action: "throw" };
    },
    dismissCatchResult: async () => { calls.push({ path: "dismiss" }); },
  };
  runInNewContext(workflow + capture, context);
  return { context, calls };
}

for (const state of [
  { is_map_screen: true, is_encounter: true, is_catch_summary: true },
  { is_catch_summary: true },
  { is_pokemon_detail: true },
  {},
]) {
  test("no automatic input outside encounter: " + JSON.stringify(state), async () => {
    const f = fixture([state]);
    await f.context.captureAndDetectScreen();
    assert.equal(f.calls.filter(c => c.body).length, 0);
    assert.equal(f.calls.some(c => c.path === "dismiss"), Boolean(!state.is_map_screen && state.is_catch_summary));
    const g = fixture([state]);
    await g.context.runAutoCatchWorkflow("device");
    assert.equal(g.calls.filter(c => c.body).length, 0);
    assert.equal(g.calls.some(c => c.path === "dismiss"), Boolean(!state.is_map_screen && state.is_catch_summary));
  });
}

for (const result of [{ is_map_screen: true, is_pokemon_detail: true },
  { is_catch_summary: true }, { is_pokemon_detail: true }, {}]) {
  test("after throw, dismiss only confirmed results: " + JSON.stringify(result), async () => {
    const f = fixture([{ is_encounter: true, ready_to_throw: true }, result]);
    await f.context.runAutoCatchWorkflow("device");
    assert.deepEqual(f.calls.filter(c => c.body).map(c => c.path), ["/api/screen/throw_ball"]);
    assert.equal(f.calls.some(c => c.path === "dismiss"), Boolean(!result.is_map_screen && (result.is_catch_summary || result.is_pokemon_detail)));
    assert.equal(f.context.activeCatchWorkflow, false);
  });
}

test("after throw waits through transient map until catch summary", async () => {
  const f = fixture([
    { is_encounter: true, ready_to_throw: true },
    { is_map_screen: true },
    { is_map_screen: true },
    { is_catch_summary: true },
  ]);
  await f.context.runAutoCatchWorkflow("device");
  assert.deepEqual(f.calls.filter(c => c.body).map(c => c.path), ["/api/screen/throw_ball"]);
  assert.equal(f.calls.some(c => c.path === "dismiss"), true);
});

test("after catch keeps closing summary/detail for follow-up window", async () => {
  const f = fixture([
    { is_encounter: true, ready_to_throw: true },
    { is_catch_summary: true },
    { is_pokemon_detail: true },
    { is_map_screen: true },
  ]);
  await f.context.runAutoCatchWorkflow("device");
  assert.deepEqual(f.calls.filter(c => c.path === "dismiss").length, 2);
});

test("PokéStop screen blocks Auto-Catch even if ready flag is wrong", async () => {
  const f = fixture([{ is_pokestop_spin_screen: true, ready_to_throw: true, is_encounter: true }]);
  await f.context.captureAndDetectScreen();
  assert.equal(f.calls.filter(c => c.body).length, 0);

  const g = fixture([{ is_pokestop_spin_screen: true, ready_to_throw: true, is_encounter: true }]);
  await g.context.runAutoCatchWorkflow("device");
  assert.equal(g.calls.filter(c => c.body).length, 0);
});

test("queued workflow does nothing after disabling Auto-Catch", async () => {
  const f = fixture([{ is_encounter: true }]);
  f.context.isAutoCatchEnabled = () => false;
  await f.context.runAutoCatchWorkflow("device");
  assert.equal(f.calls.length, 0);
});

test("Auto-Spin spins eligible candidate on map screen", async () => {
  const f = fixture([{ is_map_screen: true }]);
  f.context.isAutoSpinEnabled = () => true;
  f.context.lastSpinTimestamp = 0;
  f.context.chooseNextMapTarget = () => ({ kind: "stop", target: { x: 0.5, y: 0.5 } });
  f.context.chooseCandidate = () => ({ x: 0.5, y: 0.5 });
  let spins = 0;
  f.context.runQuickSpinWorkflow = async () => { spins++; };
  await f.context.captureAndDetectScreen();
  assert.equal(spins, 1);
});

test("map target picker alternates pokemon and stop when both are available", async () => {
  const f = fixture([{ is_map_screen: true }, { is_map_screen: true }]);
  f.context.isAutoSpinEnabled = () => true;
  f.context.isAutoPokemonEnabled = () => true;
  f.context.lastSpinTimestamp = 0;
  f.context.chooseNextMapTarget = () => {
    return f.context.lastAutoTargetKind === "pokemon"
      ? { kind: "stop", target: { x: 0.5, y: 0.5 } }
      : { kind: "pokemon", target: { score: 90 } };
  };
  f.context.clickDetectedPokemon = async () => true;
  let spins = 0;
  f.context.runQuickSpinWorkflow = async () => { spins++; };

  await f.context.captureAndDetectScreen();
  assert.equal(f.context.lastAutoTargetKind, "pokemon");

  f.context.screenBusy = false;
  await f.context.captureAndDetectScreen();
  assert.equal(spins, 1);
  assert.equal(f.context.lastAutoTargetKind, "stop");
});

test("opening Auto-Catch does not disable Auto-Spin", () => {
  const f = fixture([]);
  let spinEnabled = true;
  f.context.syncAutoCatchToggles = () => {};
  f.context.syncAutoSpinToggles = (value) => { spinEnabled = value; };
  const start = source.indexOf("function handleAutoCatchToggle(");
  const end = source.indexOf('\n$("#screen-auto-catch-chk")', start);
  runInNewContext(source.slice(start, end), f.context);
  f.context.handleAutoCatchToggle(true);
  assert.equal(spinEnabled, true);
});

test("spin does not start while activeCatchWorkflow is in progress", async () => {
  const f = fixture([]);
  f.context.activeCatchWorkflow = true;
  const start = source.indexOf("async function runQuickSpinWorkflow(");
  const end = source.indexOf("// Screen Studio Event Listeners", start);
  runInNewContext(source.slice(start, end), f.context);
  await f.context.runQuickSpinWorkflow({ x: 0.5, y: 0.5 });
  assert.equal(f.calls.length, 0);
  assert.equal(f.context.activeSpinWorkflow, null);
});


test("encounter without a ready ball never sends a throw", async () => {
  const f = fixture([{is_encounter: true, ready_to_throw: false}]);
  await f.context.runAutoCatchWorkflow("device");
  assert.equal(f.calls.filter(c => c.body).length, 0);
});

test("dismiss taps only the total summary and leaves the next page", async () => {
  const f = fixture([{is_catch_summary: true, has_total_label: true}, {is_pokemon_detail: true}, {is_map_screen: true}]);
  const original = f.context.api;
  f.context.api = async (path, body) => {
    const res = await original(path, body);
    return body ? {dismissed: true} : res;
  };
  runInNewContext(source.slice(source.indexOf("async function dismissCatchResult("),
    source.indexOf("async function runAutoCatchWorkflow(")), f.context);
  await f.context.dismissCatchResult("device", true);
  assert.deepEqual(f.calls.filter(c => c.body).map(c => c.path),
    ["/api/screen/dismiss_catch_summary"]);
});
