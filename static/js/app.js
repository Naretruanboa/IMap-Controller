import { Connection } from "./websocket.js";
import { LocationMap } from "./map.js";
import { RoutePlan } from "./route.js";
import { setupJoystick } from "./joystick.js";
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
  $("#joystick").setAttribute(
    "aria-disabled",
    String(!(connected && online && current?.simulation_active)),
  );
}
const teleport = safe(async (point) => {
  if (!connected || !online) throw new Error("Connect a device first");
  stopJoystick();
  await api("/api/location/set", point);
  toast("Simulated location updated");
});
const saveFavorite = safe(async (point) => {
  if (!point) throw new Error("Select a destination first");
  const name = prompt("Favorite name");
  if (!name?.trim()) return;
  await api("/api/favorites", { ...point, name: name.trim() });
  toast("Favorite saved");
  if (mode === "favorites") loadSaved();
});
const map = new LocationMap(
  (point) => {
    destination = point;
    $("#destination-display").textContent =
      `${point.latitude.toFixed(6)}, ${point.longitude.toFixed(6)}`;
  },
  {
    teleport,
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
    joystick: "Joystick",
    two: "Two Spot",
    route: "Multi Spot",
    gpx: "Import GPX",
    favorites: "Favorites",
    history: "History",
    device: "Device",
  };
  $("#mode-title").textContent = titles[mode];
  $("#route-controls").hidden = !["two", "route", "gpx"].includes(mode);
  $("#gpx-controls").hidden = mode !== "gpx";
  $("#json-controls").hidden = mode !== "route";
  $("#saved-list").replaceChildren();
  const help = {
    teleport:
      "Click anywhere on the map or enter coordinates to choose a destination.",
    joystick:
      "Teleport to a starting point, then drag the joystick or hold WASD to move.",
    two: "Select point A and point B. Add each destination to your route.",
    route: "Select destinations and add waypoints in travel order.",
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
        : ["two", "route", "gpx"].includes(mode)
          ? "Plan your journey."
          : "Your next location.";
  $("#panel-label").textContent = ["two", "route", "gpx"].includes(mode)
    ? "ROUTE PLANNER"
    : "LOCATION CONTROL";
  if (mode === "two" && plan.points.length > 2) {
    plan.points = plan.points.slice(0, 2);
    plan.render();
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
  await teleport(destination);
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
    plan.render();
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
      $("#route-status").textContent =
        `${message.status}${message.distance_remaining !== undefined && message.distance_remaining !== null ? ` · ${message.distance_remaining.toFixed(0)} m remaining` : ""}${message.route_progress != null ? ` · ${(message.route_progress * 100).toFixed(0)}%` : ""}`;
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
plan.render();
safe(async () => {
  const state = await api("/api/state");
  $("#provider").textContent = state.provider.toUpperCase();
  await refreshDevices();
})();
setInterval(() => safe(refreshDevices)(), 5000);
