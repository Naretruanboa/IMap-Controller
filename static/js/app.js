import { Connection } from "./websocket.js";
import { LocationMap } from "./map.js";
import { RoutePlan } from "./route.js";
import { setupJoystick } from "./joystick.js";
import { detectStops } from "./stop_detector.js";
import { SpinWorkflow, analyzeScreen, chooseCandidate } from "./spin_workflow.js";
const $ = (s) => document.querySelector(s);
let connected = false,
  online = false,
  destination = null,
  mode = "teleport",
  current = null,
  routeStatus = "idle",
  deviceKey = "",
  stopJoystick = () => {};
function toast(message) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = message;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), 5000);
}
async function api(path, body, method = body === undefined ? "GET" : "POST") {
  const response = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : JSON.stringify(data.detail),
    );
  return data;
}
function safe(action) {
  return async (...args) => {
    try {
      return await action(...args);
    } catch (error) {
      toast(error.message);
    }
  };
}
function controls() {
  document
    .querySelectorAll("[data-control]")
    .forEach((el) => (el.disabled = !(connected && online)));
  $("#speed").disabled = !(connected && online);
  $("#speed-schedule").disabled = !(connected && online);
  $("#pause-route").disabled = !(connected && online && routeStatus === "running");
  $("#resume-route").disabled = !(connected && online && routeStatus === "paused");
  $("#stop-route").disabled = !(connected && online && ["running", "paused"].includes(routeStatus));
  $("#reroll-random").disabled = !(connected && online && ["running", "paused"].includes(routeStatus));
  $("#joystick").setAttribute(
    "aria-disabled",
    String(!(connected && online && current?.simulation_active)),
  );
}
const teleport = safe(async (point) => {
  if (!connected || !online) throw new Error("Connect a device first");
  stopJoystick();
  await api("/api/location/set", point);
  map.clearRoute();
  destination = point;
  try {
    localStorage.setItem("last_destination", JSON.stringify(point));
  } catch (_) {}
  if (mode === "random") {
    randomCenter = point;
    map.setRadiusCircle(point, Number($("#random-radius").value));
  }
  toast("Simulated location updated");
});
const runTo = safe(async (point) => {
  if (!connected || !online) throw new Error("Connect a device first");
  if (!current?.simulation_active) throw new Error("Set a starting location with Teleport first");
  stopJoystick();
  await api("/api/location/run", point);
  toast("Moving to destination at the selected speed");
});
const saveFavorite = safe(async (point) => {
  if (!point) throw new Error("Select a destination first");
  const name = prompt("Favorite name");
  if (!name?.trim()) return;
  await api("/api/favorites", { ...point, name: name.trim() });
  toast("Favorite saved");
  if (mode === "favorites") loadSaved();
});
let randomCenter = null;
const map = new LocationMap(
  (point) => {
    destination = point;
    $("#destination-display").textContent =
      `${point.latitude.toFixed(6)}, ${point.longitude.toFixed(6)}`;
    if (mode === "random") {
      randomCenter = point;
      const radius = Number($("#random-radius").value);
      map.setRadiusCircle(point, radius);
    }
  },
  {
    teleport,
    run: (point) => {
      setMode("run");
      return runTo(point);
    },
    route: safe((point) => {
      if (!["two", "route"].includes(mode)) setMode("route");
      plan.add(point, mode === "two");
    }),
    favorite: saveFavorite,
  },
  toast,
);
const plan = new RoutePlan(map);
const sidebarToggle = $("#sidebar-toggle");
function setSidebarCollapsed(collapsed) {
  document.body.classList.toggle("sidebar-collapsed", collapsed);
  sidebarToggle.setAttribute("aria-expanded", String(!collapsed));
  sidebarToggle.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
  sidebarToggle.title = collapsed ? "Expand sidebar" : "Collapse sidebar";
  localStorage.setItem("sidebar-collapsed", String(collapsed));
  setTimeout(() => map.map?.invalidateSize(), 220);
}
setSidebarCollapsed(localStorage.getItem("sidebar-collapsed") === "true");
sidebarToggle.onclick = () =>
  setSidebarCollapsed(!document.body.classList.contains("sidebar-collapsed"));
const panel = $("#panel");
const panelToggle = $("#panel-toggle");
function setPanelCollapsed(collapsed) {
  panel.classList.toggle("panel-collapsed", collapsed);
  panelToggle.textContent = collapsed ? "+" : "−";
  panelToggle.setAttribute("aria-expanded", String(!collapsed));
  panelToggle.setAttribute(
    "aria-label",
    collapsed ? "Expand control panel" : "Collapse control panel",
  );
  panelToggle.title = collapsed ? "Expand control panel" : "Collapse control panel";
  localStorage.setItem("panel-collapsed", String(collapsed));
}
setPanelCollapsed(localStorage.getItem("panel-collapsed") === "true");
panelToggle.onclick = () =>
  setPanelCollapsed(!panel.classList.contains("panel-collapsed"));
function setMode(next) {
  mode = next;
  document
    .querySelectorAll("[data-mode]")
    .forEach((el) => el.classList.toggle("active", el.dataset.mode === mode));
  const titles = {
    teleport: "Teleport",
    run: "Run",
    joystick: "Joystick",
    two: "Two Spot",
    route: "Multi Spot",
    random: "Random Walk",
    screen: "Screen Studio",
    "ai-trainer": "AI Studio",
    gpx: "Import GPX",
    favorites: "Favorites",
    history: "History",
    device: "Device",
  };
  $("#mode-title").textContent = titles[mode];
  $("#route-controls").hidden = !["two", "route", "gpx"].includes(mode);
  $("#random-controls").hidden = mode !== "random";
  $("#screen-controls").hidden = mode !== "screen";
  $("#ai-trainer-controls").hidden = mode !== "ai-trainer";
  $("#motion-controls").hidden = !["run", "two", "route", "gpx", "random", "screen"].includes(mode);
  $("#teleport").textContent = mode === "run" ? "Run here →" : "Teleport here ↗";
  $("#teleport").parentElement.hidden = ["random", "screen", "ai-trainer"].includes(mode);
  $("#gpx-controls").hidden = mode !== "gpx";
  $("#json-controls").hidden = mode !== "route";
  $("#saved-list").replaceChildren();
  const help = {
    run: "Set a starting location with Teleport first. Select a destination, then press Run here to move at the selected speed. Selecting another point only previews it; press Run here to change direction.",
    teleport:
      "Click anywhere on the map or enter coordinates to choose a destination.",
    joystick:
      "Teleport to a starting point, then drag the joystick or hold WASD to move.",
    two: "Select point A and point B. Add each destination to your route.",
    route: "Select destinations and add waypoints in travel order.",
    random:
      "Set a center point and radius. The system will continuously generate and walk random routes within the designated boundary.",
    screen:
      "Live PokéStop detection with AI/Heuristics. Control GPS movement and Quick Spin directly on top of the live game map.",
    "ai-trainer":
      "Collect dataset, auto-label bounding boxes, and train offline YOLOv8 model directly from this browser.",
    gpx: "Import a GPX track, route or waypoint list. Preview it before starting.",
    favorites: "Your saved locations, stored locally on this Mac.",
    history: "Recent teleports and route starts, stored locally on this Mac.",
    device:
      "Choose a device in the sidebar. Unlock it and enable its developer connection.",
  };
  $("#mode-help").textContent = help[mode];
  $("#panel-title").textContent = ["favorites", "history"].includes(mode)
    ? "Places to return to."
    : mode === "device"
      ? "Connect your device."
      : mode === "joystick"
        ? "Keep moving."
        : mode === "random"
          ? "Random Radius Patrol."
          : mode === "screen"
            ? "Live PokéStop Detection."
            : mode === "ai-trainer"
              ? "Train PokéStop AI Model."
              : ["two", "route", "gpx"].includes(mode)
                ? "Plan your journey."
                : "Your next location.";
  $("#panel-label").textContent = mode === "random"
    ? "RANDOM WALK PATROL"
    : mode === "screen"
      ? "SCREEN & AI VISION"
      : mode === "ai-trainer"
        ? "OFFLINE AI TRAINING STUDIO"
        : ["two", "route", "gpx"].includes(mode)
          ? "ROUTE PLANNER"
          : "LOCATION CONTROL";
  if (mode === "two" && plan.points.length > 2) {
    plan.points = plan.points.slice(0, 2);
    plan.render();
  }
  if (mode === "random") {
    const center = randomCenter || destination || (current?.simulation_active && current?.latitude != null ? current : null);
    if (center) {
      map.setRadiusCircle(center, Number($("#random-radius").value));
    }
  } else if (routeStatus !== "running" && routeStatus !== "paused") {
    map.clearRadiusCircle();
  }
  if (mode === "screen") {
    $("#screen-overlay").hidden = false;
    refreshScreenDevices();
    captureAndDetectScreen();
  }
  if (mode === "ai-trainer") {
    refreshDatasetStats();
    refreshModelStatus();
    pollTrainStatus();
  }
  if (["favorites", "history"].includes(mode)) loadSaved();
}
document
  .querySelectorAll("[data-mode]")
  .forEach((el) => (el.onclick = () => setMode(el.dataset.mode)));
async function refreshDevices() {
  const devices = await api("/api/devices");
  const key = JSON.stringify(devices);
  if (key === deviceKey) return;
  deviceKey = key;
  const select = $("#devices"),
    selected = select.value;
  select.replaceChildren();
  if (!devices.length) {
    const opt = new Option("No USB device found", "");
    select.add(opt);
  }
  for (const d of devices)
    select.add(new Option(`${d.name} · ${d.ios_version}`, d.udid));
  if (devices.some((d) => d.udid === selected)) select.value = selected;
}
$("#refresh").onclick = safe(refreshDevices);
$("#connect").onclick = safe(async () => {
  if (!$("#devices").value) throw new Error("No device available");
  $("#connect").disabled = true;
  try {
    await api("/api/devices/connect", { udid: $("#devices").value });
    toast("Device connected");
  } finally {
    $("#connect").disabled = false;
  }
});
$("#disconnect").onclick = safe(async () => {
  stopJoystick();
  await api("/api/devices/disconnect", {});
});
$("#reset-distance").onclick = safe(async () => {
  await api("/api/movement/distance/reset", {});
  toast("เริ่มนับระยะใหม่แล้ว");
});
$("#restore").onclick = safe(async () => {
  stopJoystick();
  await api("/api/location/clear", {});
  toast("Developer simulated location cleared");
});
$("#teleport").onclick = safe(async () => {
  if (!destination) throw new Error("Select a destination first");
  await (mode === "run" ? runTo(destination) : teleport(destination));
});
$("#favorite").onclick = () => saveFavorite(destination);
$("#search-form").onsubmit = safe(async (event) => {
  event.preventDefault();
  const query = $("#search").value.trim();
  const parts = query.split(/[,\s]+/).filter(Boolean);
  const [latitude, longitude] = parts.map(Number);
  if (
    parts.length === 2 &&
    Number.isFinite(latitude) &&
    Number.isFinite(longitude) &&
    Math.abs(latitude) <= 90 &&
    Math.abs(longitude) <= 180
  ) {
    $("#search-results").hidden = true;
    map.select({ latitude, longitude }, true);
    return;
  }
  if (query.length < 2)
    throw new Error("Enter a place name or latitude, longitude.");
  const submit = $("#search-form button");
  submit.disabled = true;
  submit.textContent = "Searching…";
  try {
    const data = await api(
      `/api/search?q=${encodeURIComponent(query)}&language=${encodeURIComponent(navigator.language || "en")}`,
    );
    const results = $("#search-results");
    results.replaceChildren();
    if (!data.results.length) {
      const empty = document.createElement("p");
      empty.textContent = "No places found. Try a more specific name.";
      results.append(empty);
    }
    for (const result of data.results) {
      const button = document.createElement("button");
      const title = document.createElement("span");
      title.textContent = result.name;
      const details = document.createElement("small");
      details.textContent = `${result.latitude.toFixed(5)}, ${result.longitude.toFixed(5)}`;
      button.append(title, details);
      button.onclick = () => {
        map.select(result, true);
        results.hidden = true;
      };
      results.append(button);
    }
    results.hidden = false;
  } finally {
    submit.disabled = false;
    submit.textContent = "Go ↵";
  }
});
$("#center").onclick = () => map.center();
$("#locate-mac").onclick = safe(async () => {
  if (!navigator.geolocation)
    throw new Error("This browser does not provide location access.");
  const position = await new Promise((resolve, reject) =>
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      timeout: 15000,
      maximumAge: 30000,
    }),
  );
  const point = {
    latitude: position.coords.latitude,
    longitude: position.coords.longitude,
  };
  map.select(point, true);
  toast(
    `Using this Mac's location (accuracy about ${Math.round(position.coords.accuracy)} m). This is not read from iPhone GPS.`,
  );
});
$("#add-waypoint").onclick = safe(() => {
  if (!destination) throw new Error("Select a destination first");
  plan.add(destination, mode === "two");
});
$("#clear-route").onclick = () => {
  plan.points = [];
  plan.render();
};
function parseWaypointFile(payload) {
  const points = Array.isArray(payload)
    ? payload
    : payload?.waypoints ?? payload?.points;
  if (!Array.isArray(points) || points.length < 1)
    throw new Error("JSON must contain a non-empty waypoints array");
  if (points.length > 10000)
    throw new Error("Maximum 10,000 waypoints");
  return points.map((point, index) => {
    const latitude = point?.latitude;
    const longitude = point?.longitude;
    if (
      !Number.isFinite(latitude) ||
      !Number.isFinite(longitude) ||
      latitude < -90 ||
      latitude > 90 ||
      longitude < -180 ||
      longitude > 180
    )
      throw new Error(`Invalid coordinates at waypoint ${index + 1}`);
    return { latitude, longitude };
  });
}
$("#export-waypoints").onclick = safe(() => {
  if (!plan.points.length) throw new Error("Add at least one waypoint before exporting");
  const payload = {
    format: "location-studio-waypoints",
    version: 1,
    exported_at: new Date().toISOString(),
    loops: Number($("#loops").value),
    waypoints: plan.points.map(({ latitude, longitude }) => ({ latitude, longitude })),
  };
  const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], {
    type: "application/json",
  });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = `location-studio-waypoints-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 0);
  toast(`Exported ${plan.points.length} waypoints`);
});
$("#import-waypoints").onchange = safe(async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    if (file.size > 2_000_000) throw new Error("JSON must be smaller than 2 MB");
    let payload;
    try {
      payload = JSON.parse(await file.text());
    } catch {
      throw new Error("The selected file is not valid JSON");
    }
    const points = parseWaypointFile(payload);
    plan.points = points;
    if (Number.isInteger(payload?.loops) && payload.loops >= 0 && payload.loops <= 1000)
      $("#loops").value = String(payload.loops);
    plan.render();
    map.fit(points);
    toast(`Imported ${points.length} waypoints`);
  } finally {
    event.target.value = "";
  }
});
$("#use-current").onclick = safe(() => {
  if (!current?.simulation_active || current.latitude == null)
    throw new Error("No controlled location available. Select point A on the map first.");
  if (plan.points.length)
    throw new Error("Clear the planned route before choosing a new starting point");
  plan.add(current, mode === "two");
});
$("#loops").onchange = () => plan.render();
function updateRandomRadius(radius) {
  $("#random-radius").value = radius;
  $("#random-radius-display").textContent = `${radius >= 1000 ? (radius / 1000).toFixed(1) + " km" : radius + " m"}`;
  document.querySelectorAll(".radius-presets [data-radius]").forEach((btn) => {
    btn.classList.toggle("selected", Number(btn.dataset.radius) === radius);
  });
  const center = randomCenter || destination || (current?.simulation_active && current?.latitude != null ? current : null);
  if (center) {
    map.setRadiusCircle(center, radius);
  }
}
document.querySelectorAll(".radius-presets [data-radius]").forEach((btn) => {
  btn.onclick = () => updateRandomRadius(Number(btn.dataset.radius));
});
$("#random-radius").oninput = (e) => {
  updateRandomRadius(Number(e.target.value));
};
$("#random-points").oninput = (e) => {
  $("#random-points-display").textContent = e.target.value;
};
$("#random-use-current").onclick = safe(() => {
  if (!current?.simulation_active || current.latitude == null)
    throw new Error("No controlled location available. Teleport or select a point on the map first.");
  randomCenter = { latitude: current.latitude, longitude: current.longitude };
  destination = randomCenter;
  $("#destination-display").textContent = `${randomCenter.latitude.toFixed(6)}, ${randomCenter.longitude.toFixed(6)}`;
  map.select(randomCenter, true);
  map.setRadiusCircle(randomCenter, Number($("#random-radius").value));
  toast("Set current location as random walk center");
});
let previewedRandomPoints = null;
$("#preview-random").onclick = safe(async () => {
  const center = randomCenter || destination || (current?.simulation_active && current?.latitude != null ? current : null);
  if (!center) throw new Error("Select a center location on the map first");
  const radius = Number($("#random-radius").value);
  const points = Number($("#random-points").value);
  map.setRadiusCircle(center, radius);
  const data = await api("/api/routes/random/preview", {
    center: { latitude: center.latitude, longitude: center.longitude },
    radius_m: radius,
    point_count: points,
    continuous: $("#random-continuous").checked,
  });
  if (data?.points?.length) {
    previewedRandomPoints = data.points;
    map.route(data.points);
    toast(`สุ่มสร้างเส้นทางตัวอย่าง ${data.points.length} จุดเรียบร้อย ตรวจสอบบนแผนที่แล้วกด Start เพื่อเริ่มเดิน`);
  }
});
$("#start-random").onclick = safe(async () => {
  const center = randomCenter || destination || (current?.simulation_active && current?.latitude != null ? current : null);
  if (!center) throw new Error("Select a center location on the map first");
  stopJoystick();
  const radius = Number($("#random-radius").value);
  const points = Number($("#random-points").value);
  const continuous = $("#random-continuous").checked;
  map.setRadiusCircle(center, radius);
  const payload = {
    center: { latitude: center.latitude, longitude: center.longitude },
    radius_m: radius,
    point_count: points,
    continuous: continuous,
  };
  if (previewedRandomPoints && previewedRandomPoints.length === points) {
    payload.initial_points = previewedRandomPoints;
  }
  await api("/api/routes/random", payload);
  previewedRandomPoints = null;
  toast(`Started random walk (${radius >= 1000 ? (radius / 1000).toFixed(1) + " km" : radius + " m"} radius)`);
});
$("#reroll-random").onclick = safe(async () => {
  await api("/api/routes/random/reroll", {});
  previewedRandomPoints = null;
  toast("New random route generated");
});
$("#clear-random").onclick = safe(async () => {
  if (["running", "paused"].includes(routeStatus)) {
    await api("/api/routes/stop", {});
  }
  randomCenter = null;
  destination = null;
  previewedRandomPoints = null;
  $("#destination-display").textContent = "No destination selected";
  $("#random-info").hidden = true;
  map.clearDestination();
  map.clearRadiusCircle();
  map.clearRoute();
  toast("Cleared random walk and center pin. Click on the map to choose a new location.");
});
$("#start-route").onclick = safe(async () => {
  if (plan.points.length < 2) throw new Error("Add at least two waypoints first");
  if (mode === "two" && plan.points.length !== 2)
    throw new Error("Choose exactly two waypoints");
  stopJoystick();
  await api("/api/routes/start", {
    points: plan.points,
    loops: Number($("#loops").value),
  });
  toast("Route started at point A");
});
for (const action of ["pause", "resume", "stop"])
  $(`#${action}-route`).onclick = safe(() => api(`/api/routes/${action}`, {}));
$("#gpx-file").onchange = safe(async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  if (file.size > 2_000_000) throw new Error("GPX must be smaller than 2 MB");
  const data = await api("/api/gpx/import", { xml: await file.text() });
  plan.points = data.points;
  plan.render();
  map.fit(plan.points);
  toast(
    `Imported ${plan.points.length} waypoints. Review the route before starting.`,
  );
  event.target.value = "";
});
function speedDisplay(value) {
  $("#speed-value").replaceChildren(
    document.createTextNode(`${value.toFixed(1)} `),
  );
  const small = document.createElement("small");
  small.textContent = "km/h";
  $("#speed-value").append(small);
  $("#mps").textContent = `${(value / 3.6).toFixed(2)} m/s`;
  $("#speed").value = value;
  document
    .querySelectorAll("[data-speed]")
    .forEach((el) =>
      el.classList.toggle("selected", Number(el.dataset.speed) === value),
    );
  $("#custom-speed").classList.toggle("selected", ![5, 10, 15].includes(value));
  if (plan.speed !== value) {
    plan.speed = value;
    plan.render(false);
  }
}
const SPEED_SCHEDULES = {
  ramp: [
    { kmh: 3, seconds: 15 },
    { kmh: 5, seconds: 15 },
    { kmh: 8, seconds: 15 },
  ],
  steps: [
    { kmh: 2, seconds: 10 },
    { kmh: 6, seconds: 10 },
  ],
};
let speedScheduleTimer = null;
let speedScheduleIndex = 0;
let serverSpeedSchedule = "off";
function cancelSpeedSchedule(resetSelection = true) {
  clearTimeout(speedScheduleTimer);
  speedScheduleTimer = null;
  speedScheduleIndex = 0;
  if (resetSelection) $("#speed-schedule").value = "off";
  $("#speed-schedule-status").textContent = "Constant";
}
function changeSpeed(value, scheduled = false) {
  if (!connected || !online) {
    toast("Connect a device first");
    return;
  }
  if (!scheduled) cancelSpeedSchedule();
  connection.send({ type: "speed", kmh: value });
  speedDisplay(value);
}
function runSpeedSchedulePhase() {
  const name = $("#speed-schedule").value;
  const phases = SPEED_SCHEDULES[name];
  if (!phases || !connected || !online) {
    cancelSpeedSchedule(!phases);
    return;
  }
  const phase = phases[speedScheduleIndex];
  changeSpeed(phase.kmh, true);
  $("#speed-schedule-status").textContent =
    `Phase ${speedScheduleIndex + 1}/${phases.length} · ${phase.seconds}s`;
  speedScheduleIndex = (speedScheduleIndex + 1) % phases.length;
  speedScheduleTimer = setTimeout(runSpeedSchedulePhase, phase.seconds * 1000);
}
$("#speed-schedule").onchange = () => {
  cancelSpeedSchedule(false);
  if ($("#speed-schedule").value === "target10k") {
    connection.send({ type: "speed", kmh: 5, schedule: "target10k" });
  } else {
    connection.send({ type: "speed", kmh: Number($("#speed").value) });
    if ($("#speed-schedule").value !== "off") runSpeedSchedulePhase();
  }
};
$("#speed").oninput = (e) => changeSpeed(Number(e.target.value));
document
  .querySelectorAll("[data-speed]")
  .forEach((el) => (el.onclick = () => changeSpeed(Number(el.dataset.speed))));
$("#custom-speed").onclick = safe(() => {
  const raw = prompt("Speed in km/h (0.1–200)", $("#speed").value);
  if (raw === null) return;
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 0.1 || value > 200)
    throw new Error("Speed must be between 0.1 and 200 km/h");
  changeSpeed(value);
});
const loadSaved = safe(async () => {
  const requested = mode,
    items = await api(`/api/${mode}`);
  if (mode !== requested) return;
  const list = $("#saved-list");
  list.replaceChildren();
  let previousGroup = "";
  for (const item of items) {
    if (mode === "history") {
      const date = new Date(item.timestamp),
        today = new Date();
      const days = Math.floor(
        (new Date(today.getFullYear(), today.getMonth(), today.getDate()) -
          new Date(date.getFullYear(), date.getMonth(), date.getDate())) /
          86400000,
      );
      const group = days === 0 ? "Today" : days === 1 ? "Yesterday" : "Older";
      if (group !== previousGroup) {
        const h = document.createElement("h3");
        h.textContent = group;
        list.append(h);
        previousGroup = group;
      }
    }
    const row = document.createElement("div");
    row.className = "saved-item";
    const title = document.createElement("strong");
    title.textContent = item.name || item.label;
    const coords = document.createElement("p");
    coords.textContent = `${item.latitude.toFixed(5)}, ${item.longitude.toFixed(5)}`;
    row.append(title, coords);
    const actions = document.createElement("div");
    actions.className = "row";
    const point = { latitude: item.latitude, longitude: item.longitude };
    for (const [label, action] of [
      ["Teleport", () => teleport(point)],
      [
        "Route",
        () => {
          setMode("route");
          plan.points = [];
          if (current?.latitude !== null && current?.latitude !== undefined)
            plan.add({
              latitude: current.latitude,
              longitude: current.longitude,
            });
          plan.add(point);
          map.select(point, true);
        },
      ],
    ]) {
      const button = document.createElement("button");
      button.textContent = label;
      button.onclick = safe(action);
      actions.append(button);
    }
    if (mode === "favorites") {
      const button = document.createElement("button");
      button.textContent = "Delete";
      button.onclick = safe(async () => {
        await api(`/api/favorites/${item.id}`, undefined, "DELETE");
        loadSaved();
      });
      actions.append(button);
    }
    row.append(actions);
    list.append(row);
  }
  if (!items.length) {
    const p = document.createElement("p");
    p.textContent = "No saved locations yet.";
    list.append(p);
  }
});
const connection = new Connection(
  (message) => {
    if (message.type === "device_state") {
      connected = message.connected;
      const d = message.device;
      $("#device-name").textContent = d?.name || "No device selected";
      $("#device-status").textContent = message.status;
      $("#device-dot").classList.toggle("connected", connected);
      $("#ios").textContent = d?.ios_version || "—";
      $("#transport").textContent = d?.connection || "—";
      $("#developer").textContent =
        d?.developer_mode === true
          ? "Enabled"
          : d?.developer_mode === false
            ? "Disabled"
            : "Unknown";
      $("#udid").textContent = d?.udid || "";
      if (!connected) {
        stopJoystick();
        cancelSpeedSchedule();
      }
      controls();
    }
    if (message.type === "location_state") {
      current = message;
      map.update(message);
      if (message.latitude != null && message.longitude != null) {
        const pt = { latitude: message.latitude, longitude: message.longitude };
        try {
          localStorage.setItem("last_location", JSON.stringify(pt));
          if (!destination) {
            destination = pt;
            $("#destination-display").textContent =
              `${pt.latitude.toFixed(6)}, ${pt.longitude.toFixed(6)}`;
            map.select(pt, false, false);
          }
        } catch (_) {}
      }
      $("#latitude").textContent = message.latitude?.toFixed(6) || "—";
      $("#longitude").textContent = message.longitude?.toFixed(6) || "—";
      $("#bearing").textContent = `${message.bearing.toFixed(0)}°`;
      $("#movement").textContent = message.moving ? "Moving" : "Idle";
      $("#simulation-label").textContent = message.restore_pending
        ? "Restore pending reconnect"
        : message.simulation_active
          ? "Simulation active"
          : "Simulation inactive";
      $("#footer-status").textContent = message.simulation_active
        ? "Developer simulation active"
        : "Ready when you are";
      speedDisplay(message.speed_kmh);
      const distance = message.distance_m || 0;
      $("#distance-value").textContent =
        `${(distance / 1000).toFixed(3)} km · ${Math.floor(distance).toLocaleString()} m`;
      const targetMode = message.speed_schedule === "target10k";
      const scheduleChanged = message.speed_schedule !== serverSpeedSchedule;
      serverSpeedSchedule = message.speed_schedule;
      $("#target-speed-note").hidden = !targetMode;
      if (targetMode) {
        clearTimeout(speedScheduleTimer);
        if (scheduleChanged) $("#speed-schedule").value = "target10k";
        const elapsed = Math.floor(message.speed_schedule_elapsed);
        $("#speed-schedule-status").textContent =
          `Auto · ${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")} · Continuous`;
      } else if (scheduleChanged && $("#speed-schedule").value === "target10k") {
        cancelSpeedSchedule();
      }
      controls();
    }
    if (message.type === "route_state") {
      routeStatus = message.status;
      controls();
      if (message.route_type === "random" && ["running", "paused"].includes(message.status)) {
        $("#random-info").hidden = false;
        $("#random-cycle-val").textContent = `#${message.cycle || 1}`;
        $("#random-waypoint-val").textContent = `${(message.current_segment || 0) + 1}/${message.point_count || message.points?.length || "?"}`;
        const walked = message.total_travelled || 0;
        $("#random-walked-val").textContent =
          walked >= 1000 ? `${(walked / 1000).toFixed(3)} km` : `${walked.toFixed(0)} m`;
        if (message.points?.length) {
          map.route(message.points);
        }
        if (!randomCenter && message.center && message.radius_m) {
          map.setRadiusCircle(message.center, message.radius_m);
        }
        $("#route-status").textContent = `Random Walk · Cycle #${message.cycle || 1} · ${message.status}`;
      } else {
        $("#random-info").hidden = true;
        if (["stopped", "idle", "completed"].includes(message.status) && !previewedRandomPoints) {
          map.clearRoute();
          if (mode !== "random") {
            map.clearRadiusCircle();
          } else {
            const center = randomCenter || destination;
            if (center) {
              map.setRadiusCircle(center, Number($("#random-radius").value));
            } else {
              map.clearRadiusCircle();
            }
          }
        }
        $("#route-status").textContent =
          message.status === "idle"
            ? "Ready to plan"
            : `${message.status}${message.distance_remaining !== undefined && message.distance_remaining !== null ? ` · ${message.distance_remaining.toFixed(0)} m remaining` : ""}${message.route_progress != null ? ` · ${(message.route_progress * 100).toFixed(0)}%` : ""}`;
      }
    }
    if (message.type === "error") toast(message.message);
  },
  (ready) => {
    online = ready;
    $("#socket-status").textContent = ready
      ? "● Live connection"
      : "Reconnecting…";
    if (!ready) {
      serverSpeedSchedule = "off";
      stopJoystick();
      cancelSpeedSchedule();
      connected = false;
    }
    controls();
  },
);
stopJoystick = setupJoystick(
  (message) => connection.send(message),
  () =>
    connected &&
    online &&
    current?.simulation_active &&
    !current?.restore_pending,
);
controls();
if (["two", "route"].includes(mode)) plan.render();
safe(async () => {
  const state = await api("/api/state");
  $("#provider").textContent = state.provider.toUpperCase();
  await refreshDevices();

  // Restore last destination / coordinates on load
  let restorePoint = null;
  try {
    const savedDest = localStorage.getItem("last_destination");
    if (savedDest) {
      const parsed = JSON.parse(savedDest);
      if (Number.isFinite(parsed.latitude) && Number.isFinite(parsed.longitude)) {
        restorePoint = parsed;
      }
    }
  } catch (_) {}

  if (!restorePoint && state.last_location) {
    restorePoint = state.last_location;
  }

  if (restorePoint) {
    destination = restorePoint;
    $("#destination-display").textContent =
      `${restorePoint.latitude.toFixed(6)}, ${restorePoint.longitude.toFixed(6)}`;
    map.select(restorePoint, false, false);
    if (mode === "random") {
      randomCenter = restorePoint;
      map.setRadiusCircle(restorePoint, Number($("#random-radius").value));
    }
  }
})();
setInterval(() => safe(refreshDevices)(), 5000);

/* =========================================================================
   Screen Studio & Live Detection (Floating Overlay + GPS Control)
   ========================================================================= */
let screenFrameBitmap = null;
let screenBusy = false;
let screenLiveTimer = null;
let currentScreenBoxes = [];
let activeSpinWorkflow = null;
let lastSpinTimestamp = 0;
let activePokemonTap = false;
let lastPokemonTapTimestamp = 0;

function syncLiveToggles(isLive) {
  if ($("#screen-live-chk")) $("#screen-live-chk").checked = isLive;
  if ($("#screen-live-toggle")) $("#screen-live-toggle").checked = isLive;
}

function syncAutoSpinToggles(isAuto) {
  if ($("#screen-auto-spin-chk")) $("#screen-auto-spin-chk").checked = isAuto;
  if ($("#screen-auto-spin-toggle")) $("#screen-auto-spin-toggle").checked = isAuto;
}

function syncAutoPokemonToggles(isAuto) {
  if ($("#screen-auto-pokemon-chk")) $("#screen-auto-pokemon-chk").checked = isAuto;
  if ($("#screen-auto-pokemon-toggle")) $("#screen-auto-pokemon-toggle").checked = isAuto;
}

function syncAutoCatchToggles(isAuto) {
  if ($("#screen-auto-catch-chk")) $("#screen-auto-catch-chk").checked = isAuto;
  if ($("#screen-auto-catch-toggle")) $("#screen-auto-catch-toggle").checked = isAuto;
  if ($("#throw-auto-catch-chk")) $("#throw-auto-catch-chk").checked = isAuto;
}

function isLiveEnabled() {
  return Boolean($("#screen-live-chk")?.checked || $("#screen-live-toggle")?.checked);
}

function isAutoSpinEnabled() {
  return Boolean($("#screen-auto-spin-chk")?.checked || $("#screen-auto-spin-toggle")?.checked);
}

function isAutoPokemonEnabled() {
  return Boolean(
    $("#screen-auto-pokemon-chk")?.checked ||
    $("#screen-auto-pokemon-toggle")?.checked
  );
}

function isAutoCatchEnabled() {
  return Boolean(
    $("#screen-auto-catch-chk")?.checked ||
    $("#screen-auto-catch-toggle")?.checked ||
    $("#throw-auto-catch-chk")?.checked
  );
}

async function refreshScreenDevices() {
  try {
    const list = await api("/api/screen/devices");
    const select = $("#screen-device-select");
    if (!select) return;
    const prev = select.value;
    select.replaceChildren();
    if (!list.length) {
      select.add(new Option("No Android device (ADB)", ""));
    }
    for (const serial of list) {
      select.add(new Option(serial, serial));
    }
    if (list.includes(prev)) select.value = prev;
    const statusText = $("#screen-status-text");
    if (statusText) {
      statusText.textContent = list.length
        ? "พร้อมจับภาพ — เลือกอุปกรณ์และกด 'จับภาพ' หรือเปิด Live"
        : "ไม่พบ Android ที่เชื่อมต่อ (กรุณาเปิด ADB บน Emulator หรือเสียบสาย USB)";
    }
  } catch (err) {
    const statusText = $("#screen-status-text");
    if (statusText) statusText.textContent = err.message;
  }
}

async function detectScreenBoxes(frameBitmap, serial, engine) {
  if (engine === "ai" && serial) {
    try {
      const res = await api(`/api/screen/detect_stops?serial=${encodeURIComponent(serial)}&engine=ai`);
      if (res.ok && res.boxes && res.boxes.length > 0) {
        return res.boxes;
      }
    } catch (e) {
      console.warn("AI detection fallback to local heuristic:", e);
    }
  }

  // Heuristic / fallback local detection
  const work = document.createElement("canvas");
  work.width = Math.min(900, frameBitmap.width);
  work.height = Math.round((frameBitmap.height * work.width) / frameBitmap.width);
  const wc = work.getContext("2d", { willReadFrequently: true });
  wc.drawImage(frameBitmap, 0, 0, work.width, work.height);
  const rawBoxes = detectStops(wc.getImageData(0, 0, work.width, work.height), 12);
  const sx = frameBitmap.width / work.width;
  const sy = frameBitmap.height / work.height;
  return rawBoxes.map((b) => ({
    ...b,
    x: Math.round(b.x * sx),
    y: Math.round(b.y * sy),
    width: Math.round(b.width * sx),
    height: Math.round(b.height * sy),
    targetX: Math.round(b.targetX * sx),
    targetY: Math.round(b.targetY * sy),
  }));
}

async function renderScreenCanvas(boxes) {
  const canvas = $("#live-screen-canvas");
  if (!canvas || !screenFrameBitmap) return;
  const ctx = canvas.getContext("2d");
  canvas.width = screenFrameBitmap.width;
  canvas.height = screenFrameBitmap.height;
  ctx.drawImage(screenFrameBitmap, 0, 0);
  canvas.hidden = false;
  $("#screen-empty-placeholder").hidden = true;

  const threshold = Number($("#screen-threshold").value);
  const showRejected = $("#screen-show-all-chk")?.checked;
  const sx = screenFrameBitmap.width / 900.0;
  ctx.lineWidth = Math.max(2, sx * 3);
  ctx.font = `bold ${Math.round(13 * Math.max(1, sx))}px sans-serif`;

  const visible = boxes
    .filter((b) => b.kind === "ring" || b.kind === "pokemon" || b.eligible || showRejected)
    .sort((a, b) => b.score - a.score);
  visible.forEach((box, index) => {
    const x = Math.max(0, box.x - 4),
      y = Math.max(0, box.y - 4),
      w = box.width + 8,
      h = box.height + 8;
    const color =
      box.kind === "pokemon" || box.class_name === "pokemon"
        ? "#f59e0b"
        : box.kind === "solid"
          ? "#c2c9ce"
          : box.color === "purple" || box.class_name === "pokestop_cooldown"
            ? "#c88aff"
            : box.score >= threshold
              ? "#49ef88"
              : "#ffdb38";
    ctx.strokeStyle = color;
    ctx.strokeRect(x, y, w, h);
    const tagW = Math.max(100, 120 * sx);
    ctx.fillStyle = color;
    ctx.fillRect(x, Math.max(0, y - 20 * sx), tagW, 20 * sx);
    ctx.fillStyle = box.kind === "pokemon" || box.class_name === "pokemon" ? "#ffffff" : "#18251d";
    const labelPrefix = box.class_name === "pokemon" ? "★ Pokemon" : box.class_name ? box.class_name : "";
    ctx.fillText(
      `#${index + 1} ${labelPrefix} ${box.score}%`,
      x + 4,
      Math.max(15 * sx, y - 4 * sx),
    );
  });

  const eligibleStops = boxes.filter((b) => b.eligible && b.class_name === "pokestop_active" && b.score >= threshold).length;
  const pokemonCount = boxes.filter((b) => b.class_name === "pokemon" || b.kind === "pokemon").length;
  $("#screen-overlay-badge").textContent = `${eligibleStops} เสาฟ้า · ${pokemonCount} โปเกมอน`;
  const listContainer = $("#screen-detected-list");
  if (listContainer) {
    listContainer.replaceChildren();
    visible.forEach((box, idx) => {
      const isPoke = box.class_name === "pokemon" || box.kind === "pokemon";
      const item = document.createElement("div");
      item.className = "detected-stop-item";
      item.innerHTML = `
        <div class="stop-badge ${isPoke ? "pokemon" : box.eligible && box.score >= threshold ? "active" : "inactive"}">#${idx + 1}</div>
        <div class="stop-details">
          <div class="stop-title">${isPoke ? "🌟 โปเกมอนป่า (Wild Pokémon)" : box.class_name || (box.color === "purple" ? "เสาคูลดาวน์ (ม่วง)" : "เสาพร้อมหมุน (ฟ้า)")} · <strong>${box.score}%</strong></div>
          <div class="stop-reason hint">${box.reason || `พิกัด (${box.targetX || box.x}, ${box.targetY || box.y})`}</div>
        </div>
      `;
      item.onclick = () => {
        const normTarget = {
          x: (box.targetX || box.x + box.width / 2) / screenFrameBitmap.width,
          y: (box.targetY || box.y + box.height / 2) / screenFrameBitmap.height,
        };
        if (isPoke) {
          toast(`แตะที่โปเกมอน #${idx + 1} เพื่อเข้าหน้าจับ...`);
          // Send direct tap to encounter pokemon
          const serial = $("#screen-device-select")?.value;
          if (serial) {
            api("/api/screen/tap", {
              serial: serial,
              x: normTarget.x,
              y: normTarget.y
            }).then(() => {
              appendSpinLog("encounter", `แตะโปเกมอนที่ (${Math.round(normTarget.x * 100)}%, ${Math.round(normTarget.y * 100)}%)`);
            });
          }
        } else {
          runQuickSpinWorkflow(normTarget);
        }
      };
      listContainer.append(item);
    });
  }
}

function appendSpinLog(step, message) {
  const timestamp = new Date().toLocaleTimeString();
  const logLine = `[${timestamp}] [${step.toUpperCase()}] ${message}`;

  const spinLogEl = $("#screen-spin-log");
  if (spinLogEl) {
    if (spinLogEl.textContent.trim() === "พร้อมบันทึกการทำงาน…") {
      spinLogEl.textContent = logLine;
    } else {
      const lines = spinLogEl.textContent.split("\n");
      if (lines.length > 100) lines.shift();
      lines.push(logLine);
      spinLogEl.textContent = lines.join("\n");
    }
    spinLogEl.scrollTop = spinLogEl.scrollHeight;
  }

  const overlayLogEl = $("#screen-overlay-log");
  if (overlayLogEl) {
    overlayLogEl.textContent = `⚡ [${step}] ${message}`;
  }

  const statusText = $("#screen-status-text");
  if (statusText) {
    statusText.textContent = `[${step}] ${message}`;
  }
}

async function clickDetectedPokemon(serial, boxes) {
  if (!isAutoPokemonEnabled() || activePokemonTap || Date.now() - lastPokemonTapTimestamp < 8000) {
    return false;
  }

  const target = chooseDetectedPokemon(boxes);
  if (!target) return false;

  const width = screenFrameBitmap?.width || 1;
  const height = screenFrameBitmap?.height || 1;
  const targetX = target.targetX ?? target.x + target.width / 2;
  const targetY = target.targetY ?? target.y + target.height / 2;
  const point = {
    x: Math.max(0, Math.min(1, targetX / width)),
    y: Math.max(0, Math.min(1, targetY / height)),
  };

  activePokemonTap = true;
  lastPokemonTapTimestamp = Date.now();
  try {
    appendSpinLog("auto-pokemon", `🌟 พบ Pokémon (${target.score}%) — กำลังคลิกเพื่อเข้า Encounter`);
    await api("/api/screen/input", { serial, action: "tap", ...point });
    return true;
  } catch (err) {
    appendSpinLog("error", `คลิก Pokémon ไม่สำเร็จ: ${err.message || err}`);
    return false;
  } finally {
    activePokemonTap = false;
  }
}

function chooseDetectedPokemon(boxes) {
  const threshold = Number($("#screen-threshold")?.value || 40);
  return boxes
    .filter((box) => (box.class_name === "pokemon" || box.kind === "pokemon") && box.score >= threshold)
    .sort((a, b) => b.score - a.score)[0];
}

let activeCatchWorkflow = false;
let lastCatchTimestamp = 0;

async function captureAndDetectScreen() {
  if (screenBusy || activeSpinWorkflow?.running || activeCatchWorkflow) return;
  const serial = $("#screen-device-select")?.value;
  if (!serial) return;
  screenBusy = true;
  clearTimeout(screenLiveTimer);
  try {
    const res = await fetch(`/api/screen/capture?serial=${encodeURIComponent(serial)}`, { cache: "no-store" });
    if (!res.ok) throw new Error("Capture failed");
    const blob = await res.blob();
    const nextBitmap = await createImageBitmap(blob);
    screenFrameBitmap?.close();
    screenFrameBitmap = nextBitmap;

    const engine = $("#screen-engine-select")?.value || "ai";
    currentScreenBoxes = await detectScreenBoxes(screenFrameBitmap, serial, engine);
    await renderScreenCanvas(currentScreenBoxes);
    const eligibleStops = currentScreenBoxes.filter((b) => b.eligible);
    const statusMsg = `ตรวจพบเสาพร้อมหมุน ${eligibleStops.length} จุด (${engine === "ai" ? "AI Model" : "Heuristic"})`;
    $("#screen-status-text").textContent = statusMsg;

    // Auto-Catch and Auto-Click Pokémon both verify the screen before sending input.
    if ((isAutoCatchEnabled() || isAutoPokemonEnabled()) && !activeCatchWorkflow && !activeSpinWorkflow?.running && Date.now() - lastCatchTimestamp > 2500) {
      try {
        const enc = await api("/api/screen/detect_encounter?serial=" + encodeURIComponent(serial));

        // *** MAP SCREEN GUARD — skip auto-catch entirely ***
        if (enc.is_map_screen) {
          const clickedPokemon = await clickDetectedPokemon(serial, currentScreenBoxes);
          if (clickedPokemon) return;
        } else if (enc.is_catch_summary) {
          await dismissCatchResult(serial, true);
          return;
        } else if (enc.ready_to_throw) {
          appendSpinLog("auto-catch", "🎯 ตรวจพบหน้าจอ Encounter — กำลังเริ่มโยน Pokéball อัตโนมัติ...");
          setTimeout(() => runAutoCatchWorkflow(serial), 100);
          return;
        }

      } catch (_) {}
    }

    // Auto-Spin trigger
    const pokemonHasPriority = isAutoPokemonEnabled() && Boolean(chooseDetectedPokemon(currentScreenBoxes));
    if (isAutoSpinEnabled() && !pokemonHasPriority && !activeSpinWorkflow?.running && !activeCatchWorkflow && Date.now() - lastSpinTimestamp > 5000) {
      const candidate = chooseCandidate(screenFrameBitmap, currentScreenBoxes);
      if (candidate) {
        appendSpinLog("auto-spin", "พบเสาพร้อมหมุนในระยะ — กำลังเริ่มหมุนอัตโนมัติ...");
        setTimeout(() => {
          if (isAutoSpinEnabled() && !activeSpinWorkflow?.running && !activeCatchWorkflow) runQuickSpinWorkflow(candidate);
        }, 100);
      }
    }
  } catch (err) {
    console.warn("Screen capture error:", err);
    $("#screen-status-text").textContent = `เกิดข้อผิดพลาด: ${err.message}`;
  } finally {
    screenBusy = false;
    const shouldKeepPolling = (isLiveEnabled() || isAutoCatchEnabled() || isAutoSpinEnabled()) && !document.hidden && !activeSpinWorkflow?.running && !activeCatchWorkflow;
    if (shouldKeepPolling) {
      screenLiveTimer = setTimeout(captureAndDetectScreen, 3000);
    }
  }
}

async function dismissCatchResult(serial, automatic = false) {
  if (!serial || (automatic && !isAutoCatchEnabled())) return;
  try {
    const res1 = await api("/api/screen/dismiss_catch_summary", { serial });
    if (res1.dismissed && res1.action === "ok_btn") {
      throwLog("👉 กดปุ่ม 'ตกลง' หน้าสรุปแล้ว — กำลังรอปิดหน้ารายละเอียด Pokémon...");
      await new Promise((r) => setTimeout(r, 1000));
      const res2 = await api("/api/screen/dismiss_catch_summary", { serial });
      if (res2.dismissed) {
        throwLog("👉 กดปุ่ม ✓ ปิดหน้ารายละเอียด Pokémon เรียบร้อย — กลับสู่หน้าแผนที่");
      }
    } else if (res1.dismissed && res1.action === "detail_close") {
      throwLog("👉 กดปุ่ม ✓ ปิดหน้ารายละเอียด Pokémon เรียบร้อย — กลับสู่หน้าแผนที่");
    }
  } catch (err) {
    throwLog("ปิดหน้าผลจับ: " + (err.message || err));
  }
}

async function runAutoCatchWorkflow(serial) {
  if (activeCatchWorkflow || activeSpinWorkflow?.running || !serial || !isAutoCatchEnabled()) return;
  activeCatchWorkflow = true;
  lastCatchTimestamp = Date.now();
  clearTimeout(screenLiveTimer);

  const statusEl = $("#encounter-status");
  if (statusEl) {
    statusEl.innerHTML = `🎯 <strong>พบหน้าจอ Encounter!</strong> กำลังโยน Pokéball อัตโนมัติ...`;
    statusEl.style.color = "#2ecc71";
  }

  appendSpinLog("catch", "🎯 ตรวจพบหน้าจอ Encounter! เริ่มระบบโยนบอลอัตโนมัติ...");
  throwLog("🎯 [Auto-Catch] ตรวจพบหน้าจอ Encounter! เริ่มต้นโยน Pokéball...");

  let attempts = 0;
  const maxAttempts = 20;

  while (isAutoCatchEnabled() && attempts < maxAttempts) {
    attempts++;
    try {
      // 1. Verify screen state before throwing
      const enc = await api("/api/screen/detect_encounter?serial=" + encodeURIComponent(serial));

      // *** MAP SCREEN SAFETY — abort immediately if on map ***
      if (enc.is_map_screen) {
        throwLog("🗺 ตรวจพบหน้าแผนที่ — หยุด Auto-Catch (ป้องกันชนกับหมุนเสา)");
        appendSpinLog("catch", "🗺 หน้าแผนที่ — หยุด Auto-Catch");
        if (statusEl) {
          statusEl.innerHTML = `🗺 หน้าแผนที่ — หยุด Auto-Catch แล้ว`;
          statusEl.style.color = "#70a1ff";
        }
        break;
      }

      if (enc.is_catch_summary) {
        await dismissCatchResult(serial, true);
        break;
      }
      if (!enc.ready_to_throw) {
        throwLog(`🎉 ไม่พบหน้า Encounter แล้ว — การจับเสร็จสิ้น!`);
        appendSpinLog("catch", `🎉 การจับเสร็จสิ้น`);
        toast("🎉 จบ Encounter แล้ว!");
        if (statusEl) {
          statusEl.innerHTML = `✅ จบการจับ Pokémon (โยน ${attempts - 1} ครั้ง)`;
          statusEl.style.color = "#2ecc71";
        }
        break;
      }

      if (!isAutoCatchEnabled()) break;

      const strength = Number($("#throw-strength")?.value || 50) / 100;
      const curveball = $("#throw-curveball") ? $("#throw-curveball").checked : true;
      const modeText = curveball ? "Curveball" : "Straight";

      throwLog(`🔴 [Auto-Catch] โยนครั้งที่ ${attempts} (${modeText} ${Math.round(strength * 100)}%)...`);
      appendSpinLog("throw", `[Auto-Catch] โยน ${modeText} ครั้งที่ ${attempts}...`);

      const res = await api("/api/screen/throw_ball", { serial, strength, curveball });
      throwLog(`✅ โยนสำเร็จ (${res.action})`);

      // 2. Wait for ball shake / breakout / catch resolution (poll up to 10s)
      throwLog("⏳ กำลังรอผลการจับ (รอแอนิเมชันสั่นลูกบอล)...");

      let brokeOut = false;
      let caught = false;

      // Check every 1.2s up to 10 seconds
      for (let poll = 0; poll < 8; poll++) {
        await new Promise((r) => setTimeout(r, 1200));
        if (!isAutoCatchEnabled()) break;
        try {
          const check = await api("/api/screen/detect_encounter?serial=" + encodeURIComponent(serial));
          if (check.is_map_screen) {
            caught = true;
            throwLog("🎉 กลับสู่หน้าแผนที่แล้ว!");
            break;
          }
          if (check.is_catch_summary) {
            caught = true;
            throwLog("🎉 จับ Pokémon สำเร็จ! กำลังกดตกลง...");
            await dismissCatchResult(serial, true);
            break;
          }
          if (check.is_pokemon_detail) {
            caught = true;
            throwLog("🎉 จับได้แล้ว! พบหน้ารายละเอียด — กำลังกดปุ่ม ✓ ปิด...");
            try {
              // Tap green ✓ checkmark at bottom-center (y: 0.915)
              await api("/api/screen/input", { serial, action: "tap", x: 0.50, y: 0.915 });
              await new Promise((r) => setTimeout(r, 800));
              // Tap again in case first tap didn't register
              await api("/api/screen/input", { serial, action: "tap", x: 0.50, y: 0.915 });
            } catch (_) {}
            break;
          }
          if (poll >= 2 && check.ready_to_throw) {
            brokeOut = true;
            throwLog(`⚠️ Pokémon หลุดออกจากลูกบอล! กำลังเตรียมโยนรอบที่ ${attempts + 1}...`);
            appendSpinLog("catch", `Pokémon หลุดจากลูกบอล — โยนซ้ำ`);
            break;
          }

        } catch (_) {}
      }

      if (caught) {
        break;
      }

      if (!brokeOut && !caught) {
        throwLog("⏹ ไม่ยืนยันหน้า Encounter — หยุด Auto-Catch");
        if (statusEl) {
          statusEl.textContent = "⏹ หยุด Auto-Catch — รอหน้า Encounter";
          statusEl.style.color = "#70a1ff";
        }
        break;
      }
    } catch (err) {
      throwLog(`⚠️ Auto-Catch error: ${err.message || err}`);
      await new Promise((r) => setTimeout(r, 2000));
    }
  }

  activeCatchWorkflow = false;
  lastCatchTimestamp = Date.now();

  const shouldKeepPolling = (isLiveEnabled() || isAutoCatchEnabled() || isAutoSpinEnabled()) && !document.hidden;
  if (shouldKeepPolling) {
    screenLiveTimer = setTimeout(captureAndDetectScreen, 2000);
  }
}

async function runQuickSpinWorkflow(specificTarget = null) {
  if (activeCatchWorkflow) {
    appendSpinLog("stopped", "กำลังจับ Pokémon อยู่ — รอจับเสร็จสิ้นก่อนหมุนเสา");
    return;
  }
  const serial = $("#screen-device-select")?.value;
  if (!serial) {
    appendSpinLog("error", "กรุณาเลือกอุปกรณ์ Android ก่อนหมุนเสา");
    return;
  }
  if (activeSpinWorkflow?.running) {
    appendSpinLog("busy", "กำลังดำเนินการหมุนเสาอยู่แล้ว");
    return;
  }

  lastSpinTimestamp = Date.now();
  clearTimeout(screenLiveTimer);

  activeSpinWorkflow = new SpinWorkflow({
    currentBoxes: currentScreenBoxes,
    capture: async () => {
      const res = await fetch(`/api/screen/capture?serial=${encodeURIComponent(serial)}`, { cache: "no-store" });
      if (!res.ok) throw new Error("Capture failed during workflow");
      const blob = await res.blob();
      const bmp = await createImageBitmap(blob);
      const tempCanvas = document.createElement("canvas");
      tempCanvas.width = bmp.width;
      tempCanvas.height = bmp.height;
      const ctx = tempCanvas.getContext("2d", { willReadFrequently: true });
      ctx.drawImage(bmp, 0, 0);
      const imgData = ctx.getImageData(0, 0, bmp.width, bmp.height);
      bmp.close();
      return imgData;
    },
    input: async (cmd) => {
      if (activeCatchWorkflow) {
        throw new Error("กำลังจับ Pokémon อยู่");
      }
      await api("/api/screen/input", { serial, ...cmd });
    },
    analyze: analyzeScreen,
    candidate: (image, boxes) => specificTarget || chooseCandidate(image, boxes || currentScreenBoxes),
    report: (step, msg) => {
      appendSpinLog(step, msg);
    },
  });

  try {
    await activeSpinWorkflow.run(specificTarget);
    await captureAndDetectScreen();
  } catch (e) {
    appendSpinLog("error", `Spin workflow error: ${e.message}`);
  } finally {
    activeSpinWorkflow = null;
    if (isLiveEnabled() && !$("#screen-overlay").hidden) {
      screenLiveTimer = setTimeout(captureAndDetectScreen, 2000);
    }
  }
}

// Screen Studio Event Listeners
$("#screen-device-refresh")?.addEventListener("click", refreshScreenDevices);
$("#screen-threshold")?.addEventListener("input", (e) => {
  $("#screen-thresh-val").textContent = `${e.target.value}%`;
  if (currentScreenBoxes.length) renderScreenCanvas(currentScreenBoxes);
});
$("#screen-show-all-chk")?.addEventListener("change", () => {
  if (currentScreenBoxes.length) renderScreenCanvas(currentScreenBoxes);
});
$("#screen-engine-select")?.addEventListener("change", () => captureAndDetectScreen());
$("#screen-open-overlay-btn")?.addEventListener("click", () => {
  $("#screen-overlay").hidden = false;
  captureAndDetectScreen();
});
$("#screen-overlay-close")?.addEventListener("click", () => {
  $("#screen-overlay").hidden = true;
  clearTimeout(screenLiveTimer);
});
$("#screen-overlay-dock")?.addEventListener("click", () => {
  const overlay = $("#screen-overlay");
  const isExpanded = overlay.classList.toggle("expanded");
  const dockBtn = $("#screen-overlay-dock");
  if (dockBtn) {
    dockBtn.textContent = isExpanded ? "⤡" : "⤢";
    dockBtn.title = isExpanded ? "ย่อหน้าจอลง (Normal View)" : "ขยายหน้าจอใหญ่ (Large HD View)";
  }
});
$("#screen-capture-once")?.addEventListener("click", captureAndDetectScreen);
$("#screen-clear-log-btn")?.addEventListener("click", () => {
  const el = $("#screen-spin-log");
  if (el) el.textContent = "พร้อมบันทึกการทำงาน…";
});

$("#screen-live-chk")?.addEventListener("change", (e) => {
  syncLiveToggles(e.target.checked);
  if (e.target.checked) captureAndDetectScreen();
  else clearTimeout(screenLiveTimer);
});
$("#screen-live-toggle")?.addEventListener("change", (e) => {
  syncLiveToggles(e.target.checked);
  if (e.target.checked) captureAndDetectScreen();
  else clearTimeout(screenLiveTimer);
});

$("#screen-auto-spin-chk")?.addEventListener("change", (e) => {
  syncAutoSpinToggles(e.target.checked);
  toast(e.target.checked ? "⚡ เปิดโหมดหมุนเสาอัตโนมัติ (Auto-Spin)" : "ปิดโหมดหมุนเสาอัตโนมัติ");
});
$("#screen-auto-spin-toggle")?.addEventListener("change", (e) => {
  syncAutoSpinToggles(e.target.checked);
  toast(e.target.checked ? "⚡ เปิดโหมดหมุนเสาอัตโนมัติ (Auto-Spin)" : "ปิดโหมดหมุนเสาอัตโนมัติ");
});

$("#screen-auto-pokemon-chk")?.addEventListener("change", (e) => {
  syncAutoPokemonToggles(e.target.checked);
  toast(e.target.checked ? "🌟 เปิดคลิก Pokémon อัตโนมัติ" : "ปิดคลิก Pokémon อัตโนมัติ");
});
$("#screen-auto-pokemon-toggle")?.addEventListener("change", (e) => {
  syncAutoPokemonToggles(e.target.checked);
  toast(e.target.checked ? "🌟 เปิดคลิก Pokémon อัตโนมัติ" : "ปิดคลิก Pokémon อัตโนมัติ");
});

function handleAutoCatchToggle(checked) {
  syncAutoCatchToggles(checked);
  if (checked) {
    toast("🎯 เปิดระบบ Auto-Catch (ตรวจจับ Encounter และโยน Pokéball อัตโนมัติ)");
    throwLog("⚡ เปิดโหมด Auto-Catch: พร้อมตรวจจับหน้าจอและโยนอัตโนมัติ");
    if (!screenBusy && !activeCatchWorkflow && !activeSpinWorkflow?.running) {
      captureAndDetectScreen();
    }
  } else {
    toast("ปิดระบบ Auto-Catch");
    throwLog("⏹ ปิดโหมด Auto-Catch");
  }
}

$("#screen-auto-catch-chk")?.addEventListener("change", (e) => handleAutoCatchToggle(e.target.checked));
$("#screen-auto-catch-toggle")?.addEventListener("change", (e) => handleAutoCatchToggle(e.target.checked));
$("#throw-auto-catch-chk")?.addEventListener("change", (e) => handleAutoCatchToggle(e.target.checked));

$("#quick-spin-btn")?.addEventListener("click", () => runQuickSpinWorkflow());
$("#screen-spin-workflow-btn")?.addEventListener("click", () => runQuickSpinWorkflow());

/* =========================================================================
   Pokéball Throw (Catch Pokémon)
   ========================================================================= */

// Strength slider display
$("#throw-strength")?.addEventListener("input", (e) => {
  $("#throw-strength-val").textContent = e.target.value;
});

function throwLog(msg) {
  const el = $("#throw-log");
  if (!el) return;
  const now = new Date().toLocaleTimeString("th-TH");
  if (el.innerHTML.includes("⚡ พร้อมโยน Pokéball...")) {
    el.innerHTML = "";
  }
  let color = "#f1f2f6";
  if (msg.includes("✅") || msg.includes("🎉")) color = "#2ecc71";
  else if (msg.includes("⚠️") || msg.includes("⏳")) color = "#f1c40f";
  else if (msg.includes("🔴")) color = "#ff7675";
  else if (msg.includes("❌")) color = "#ff4757";
  else if (msg.includes("🎯") || msg.includes("⚡")) color = "#70a1ff";

  el.innerHTML += `<div style="margin-bottom:2px; color:${color};">[${now}] ${msg}</div>`;
  el.scrollTop = el.scrollHeight;
}

// Detect encounter
$("#detect-encounter-btn")?.addEventListener("click", safe(async () => {
  const serial = $("#screen-device-select")?.value;
  if (!serial) { toast("เลือกอุปกรณ์ก่อน"); return; }
  const status = $("#encounter-status");
  status.innerHTML = "🔍 กำลังตรวจจับ...";
  try {
    const res = await api("/api/screen/detect_encounter?serial=" + encodeURIComponent(serial));
    if (res.is_encounter) {
      status.innerHTML = `✅ <strong>พบหน้าจอ Encounter!</strong> (${res.ball_type}) พร้อมโยน Pokéball`;
      status.style.color = "#2ecc71";
      throwLog(`✅ ตรวจพบหน้าจอ Encounter (${res.ball_type}) พร้อมโยน`);
    } else if (res.is_catch_summary) {
      status.innerHTML = `🎉 <strong>พบหน้าจอรับรางวัล XP (ตกลง)</strong> — จับสำเร็จแล้ว`;
      status.style.color = "#2ecc71";
      throwLog("🎉 ตรวจพบหน้าจอรับรางวัล XP ('ตกลง') — จับสำเร็จ");
    } else if (res.is_pokemon_detail) {
      status.innerHTML = `📋 <strong>พบหน้ารายละเอียด Pokémon</strong>`;
      status.style.color = "#70a1ff";
      throwLog("📋 ตรวจพบหน้ารายละเอียด Pokémon");
    } else if (res.is_map_screen) {
      status.innerHTML = `🗺 <strong>หน้าจอแผนที่หลัก</strong> (พร้อมค้นหา Pokémon)`;
      status.style.color = "#70a1ff";
      throwLog("🗺 หน้าจอแผนที่หลัก");
    } else {
      status.innerHTML = `❌ ไม่ใช่หน้าจอ Encounter (run:${res.has_running_man}, sky:${res.has_sky_gradient}, ball:${res.has_pokeball}, cp:${res.has_cp_text})`;
      status.style.color = "#e74c3c";
      throwLog(`❌ ไม่ใช่หน้าจอ Encounter (white_tl:${res.white_tl_ratio}, sky:${res.sky_blue_ratio}, red:${res.red_ratio})`);
    }
  } catch (err) {
    status.innerHTML = `⚠️ Error: ${err.message || err}`;
    status.style.color = "#e67e22";
  }
}));

// Manual dismiss button
$("#dismiss-catch-btn")?.addEventListener("click", safe(async () => {
  const serial = $("#screen-device-select")?.value;
  if (!serial) { toast("เลือกอุปกรณ์ก่อน"); return; }
  throwLog("👉 สั่งกดปุ่ม 'ตกลง' และปิดหน้ารายละเอียด...");
  await dismissCatchResult(serial);
  toast("✅ ส่งคำสั่งปิดหน้าต่างเรียบร้อย");
}));

// Throw mode toggle display
$("#throw-curveball")?.addEventListener("change", (e) => {
  const lbl = $("#throw-mode-label");
  if (lbl) {
    lbl.textContent = e.target.checked ? "🌀 Curveball (หมุนลูก)" : "⬆️ Straight (โยนตรง)";
  }
});

// Single throw
$("#throw-ball-btn")?.addEventListener("click", safe(async () => {
  const serial = $("#screen-device-select")?.value;
  if (!serial) { toast("เลือกอุปกรณ์ก่อน"); return; }
  const strength = Number($("#throw-strength").value) / 100;
  const curveball = $("#throw-curveball") ? $("#throw-curveball").checked : true;
  const modeText = curveball ? "Curveball" : "Straight";
  $("#throw-ball-btn").disabled = true;
  throwLog(`🔴 โยน ${modeText} ความแรง ${Math.round(strength * 100)}%...`);
  try {
    const res = await api("/api/screen/throw_ball", { serial, strength, curveball });
    throwLog(`✅ โยนสำเร็จ (${res.action})`);
    toast(`🔴 โยน Pokéball (${res.action}) แล้ว!`);
  } catch (err) {
    const detail = err.message || err;
    throwLog(`❌ ${detail}`);
    toast(`โยนไม่ได้: ${detail}`);
  } finally {
    $("#throw-ball-btn").disabled = false;
  }
}));

// Auto-throw loop
let autoThrowRunning = false;

$("#throw-ball-auto-btn")?.addEventListener("click", safe(async () => {
  const serial = $("#screen-device-select")?.value;
  if (!serial) { toast("เลือกอุปกรณ์ก่อน"); return; }

  if (autoThrowRunning) {
    autoThrowRunning = false;
    $("#throw-ball-auto-btn").textContent = "🔄 โยนจนจับได้";
    $("#throw-ball-auto-btn").style.background = "#e67e22";
    throwLog("⏹ หยุดโยนอัตโนมัติ");
    return;
  }

  autoThrowRunning = true;
  $("#throw-ball-auto-btn").textContent = "⏹ หยุดโยน";
  $("#throw-ball-auto-btn").style.background = "#c0392b";
  const strength = Number($("#throw-strength").value) / 100;
  const curveball = $("#throw-curveball") ? $("#throw-curveball").checked : true;
  const modeText = curveball ? "Curveball" : "Straight";
  let attempts = 0;
  const maxAttempts = 30;

  throwLog(`🔄 เริ่มโยนอัตโนมัติ (${modeText}, max ${maxAttempts} ครั้ง)...`);

  while (autoThrowRunning && attempts < maxAttempts) {
    attempts++;
    try {
      // 1. Verify on encounter screen before throw
      const enc = await api("/api/screen/detect_encounter?serial=" + encodeURIComponent(serial));

      // *** MAP SCREEN SAFETY — stop throwing on map ***
      if (enc.is_map_screen) {
        throwLog("🗺 ตรวจพบหน้าแผนที่ — หยุดโยนบอล (ป้องกันชนกับหมุนเสา)");
        break;
      }

      if (enc.is_catch_summary) {
        throwLog(`🎉 ตรวจพบหน้าจอรับรางวัล XP (ตกลง) — จับ Pokémon สำเร็จ!`);
        toast("🎉 จับ Pokémon สำเร็จ!");
        await dismissCatchResult(serial);
        break;
      }
      if (enc.is_pokemon_detail) {
        throwLog("🎉 ตรวจพบหน้าข้อมูล Pokémon — หยุดโยนอัตโนมัติ");
        break;
      }
      if (!enc.is_encounter) {
        throwLog(`🎉 ไม่พบหน้า Encounter แล้ว — การจับเสร็จสิ้น!`);
        toast("🎉 จบ Encounter แล้ว!");
        await dismissCatchResult(serial);
        break;
      }

      // 2. Throw ball
      throwLog(`🔴 โยนครั้งที่ ${attempts} (${modeText} ${Math.round(strength * 100)}%)...`);
      await api("/api/screen/throw_ball", { serial, strength, curveball });

      // 3. Wait for ball shake / breakout / catch resolution
      throwLog("⏳ กำลังรอผลการจับ (รอแอนิเมชันสั่นลูกบอล)...");

      let brokeOut = false;
      let caught = false;

      // Check every 1.2s up to 10s
      for (let poll = 0; poll < 8; poll++) {
        await new Promise((r) => setTimeout(r, 1200));
        if (!autoThrowRunning) break;
        try {
          const check = await api("/api/screen/detect_encounter?serial=" + encodeURIComponent(serial));
          if (check.is_catch_summary) {
            caught = true;
            throwLog(`🎉 จับ Pokémon สำเร็จ! (พบหน้ารับรางวัล XP — โยน ${attempts} ครั้ง)`);
            toast("🎉 จับ Pokémon สำเร็จ!");
            await dismissCatchResult(serial);
            break;
          }
          if (check.is_pokemon_detail) {
            caught = true;
            throwLog("🎉 จับได้แล้ว! พบหน้ารายละเอียด Pokémon — กำลังกดปิด...");
            await dismissCatchResult(serial);
            break;
          }
          if (check.is_encounter) {
            brokeOut = true;
            throwLog(`⚠️ Pokémon หลุดออกจากลูกบอล! กำลังเตรียมโยนรอบที่ ${attempts + 1}...`);
            break;
          }
          if (check.is_map_screen) {
            caught = true;
            throwLog("🎉 จับเสร็จสิ้นและกลับสู่หน้าแผนที่แล้ว!");
            break;
          }
        } catch (_) {}
      }

      if (caught) {
        break;
      }

      if (!brokeOut && !caught) {
        throwLog(`🎉 จับ Pokémon สำเร็จหรือจบการต่อสู้! (โยน ${attempts} ครั้ง)`);
        toast("🎉 จบการต่อสู้!");
        await dismissCatchResult(serial);
        break;
      }
    } catch (err) {
      throwLog(`⚠️ ครั้งที่ ${attempts}: ${err.message || err}`);
      await new Promise((r) => setTimeout(r, 2000));
    }
  }

  if (attempts >= maxAttempts) {
    throwLog(`⏹ หยุดหลังโยนครบ ${maxAttempts} ครั้ง`);
  }

  autoThrowRunning = false;
  $("#throw-ball-auto-btn").textContent = "🔄 โยนจนจับได้";
  $("#throw-ball-auto-btn").style.background = "#e67e22";
}));

/* =========================================================================
   AI Trainer Studio (Dataset Collection, Auto-Label, YOLO & ONNX Training)
   ========================================================================= */
let trainStatusTimer = null;
let datasetPollTimer = null;


async function refreshDatasetStats() {
  try {
    const data = await api("/api/ai/dataset/status");
    $("#stat-images").textContent = data.total_images ?? data.images ?? 0;
    $("#stat-labels").textContent = data.total_labels ?? data.labels ?? 0;
    $("#stat-active").textContent = data.class_counts?.pokestop_active ?? 0;
    $("#stat-cooldown").textContent = data.class_counts?.pokestop_cooldown ?? 0;
    $("#stat-gym").textContent = data.class_counts?.gym ?? 0;
    const pokeStat = $("#stat-pokemon");
    if (pokeStat) pokeStat.textContent = data.class_counts?.pokemon ?? 0;

    const isCollecting = data.collection?.running || data.is_collecting;
    if (isCollecting) {
      $("#ai-collect-progress").hidden = false;
      const target = data.collection?.total || data.target_count || 1;
      const current = data.collection?.current || data.collected_count || 0;
      const pct = Math.min(100, Math.round((current / target) * 100));
      $("#ai-collect-fill").style.width = `${pct}%`;
      $("#ai-collect-text").textContent = `${current}/${target} (${pct}%)`;
      $("#ai-start-collect").disabled = true;
      $("#ai-stop-collect").disabled = false;
      clearTimeout(datasetPollTimer);
      datasetPollTimer = setTimeout(refreshDatasetStats, 1500);
    } else {
      $("#ai-collect-progress").hidden = true;
      $("#ai-start-collect").disabled = false;
      $("#ai-stop-collect").disabled = true;
    }
  } catch (err) {
    console.warn("Dataset stats error:", err);
  }
}

async function refreshModelStatus() {
  try {
    const data = await api("/api/ai/model/status");
    const badge = $("#ai-model-ready-badge");
    const pathText = $("#ai-model-path-text");
    const isReady = data.ready || data.is_loaded;
    if (isReady) {
      badge.textContent = "Model: Active (ONNX Local)";
      badge.className = "badge badge-success";
      pathText.textContent = `${data.model_path} (${data.providers?.join(", ") || "CPU"})`;
    } else {
      badge.textContent = "Model: Fallback (Heuristic)";
      badge.className = "badge badge-warning";
      pathText.textContent = "ยังไม่มีโมเดล ONNX ที่โหลด — ระบบใช้ Heuristic ตรวจจับอัตโนมัติ";
    }
  } catch (err) {
    console.warn("Model status error:", err);
  }
}

async function pollTrainStatus() {
  clearTimeout(trainStatusTimer);
  try {
    const status = await api("/api/ai/train/status");
    const logBox = $("#ai-train-log");
    if (logBox && status.logs?.length) {
      logBox.textContent = status.logs.join("");
      logBox.scrollTop = logBox.scrollHeight;
    }
    const isTraining = Boolean(status.is_training || status.running);
    if (isTraining) {
      $("#ai-start-train-btn").disabled = true;
      $("#ai-stop-train-btn").disabled = false;
      trainStatusTimer = setTimeout(pollTrainStatus, 1000);
    } else {
      $("#ai-start-train-btn").disabled = false;
      $("#ai-stop-train-btn").disabled = true;
      const stage = status.stage || status.status;
      if (stage === "completed") {
        toast("🚀 เทรนโมเดล YOLOv8 สำเร็จและแปลงเป็น ONNX เรียบร้อย!");
        await refreshModelStatus();
      } else if (stage === "error" || stage === "failed") {
        toast(`การเทรนล้มเหลว: ${status.error || "Unknown error"}`);
      } else if (stage === "stopped") {
        toast("การเทรนถูกยกเลิกแล้ว");
      }
    }
  } catch (err) {
    console.warn("Train poll error:", err);
  }
}

$("#ai-start-collect")?.addEventListener("click", safe(async () => {
  const serial = $("#screen-device-select")?.value;
  const count = Number($("#ai-collect-count").value) || 30;
  const interval = Number($("#ai-collect-interval").value) || 2.0;
  await api("/api/ai/dataset/collect", { serial, count, interval });
  toast(`เริ่มเก็บภาพหน้าจอ ${count} ภาพทุก ๆ ${interval} วิ…`);
  refreshDatasetStats();
}));

$("#ai-stop-collect")?.addEventListener("click", safe(async () => {
  await api("/api/ai/dataset/stop-collect", {});
  toast("หยุดการเก็บภาพหน้าจอแล้ว");
  refreshDatasetStats();
}));

$("#ai-auto-label-btn")?.addEventListener("click", safe(async () => {
  $("#ai-auto-label-btn").disabled = true;
  try {
    toast("กำลังวาด Bounding Boxes ให้ชุดข้อมูลทั้งหมด…");
    const res = await api("/api/ai/dataset/auto-label", { confidence: 0.8 });
    toast(`🏷 Auto-Label สำเร็จ ${res.labeled_count} ภาพ (${res.total_boxes} boxes)`);
    await refreshDatasetStats();
  } finally {
    $("#ai-auto-label-btn").disabled = false;
  }
}));

$("#ai-start-train-btn")?.addEventListener("click", safe(async () => {
  const epochs = Number($("#ai-train-epochs").value) || 30;
  const imgsz = Number($("#ai-train-imgsz").value) || 640;
  const label_source = $("#ai-train-label-source").value || "auto";
  const logBox = $("#ai-train-log");
  if (logBox) logBox.textContent = "กำลังเตรียมสภาพแวดล้อมและเริ่มเทรนโมเดล YOLOv8 บน Mac…\n";
  await api("/api/ai/train/start", { epochs, imgsz, label_source });
  toast(`🚀 เริ่มเทรน YOLOv8 ด้วย labels: ${label_source} (${epochs} epochs, ${imgsz}px) แล้ว…`);
  pollTrainStatus();
}));

$("#ai-stop-train-btn")?.addEventListener("click", safe(async () => {
  await api("/api/ai/train/stop", {});
  toast("ส่งคำสั่งหยุดการเทรนแล้ว");
  pollTrainStatus();
}));

$("#ai-reload-model-btn")?.addEventListener("click", safe(async () => {
  await refreshModelStatus();
  toast("อัปเดตสถานะโมเดลแล้ว");
}));
