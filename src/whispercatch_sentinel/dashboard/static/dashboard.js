const dashboardConfig = JSON.parse(
  document.getElementById("dashboard-config").textContent,
);
const backendBaseUrl = dashboardConfig.backendBaseUrl.replace(/\/$/, "");
const backendHttpBase = backendBaseUrl;
const backendWsBase = backendHttpBase.replace(/^http/i, "ws");

const transcriptFilterState = {
  talkgroup: "",
  decrypted: "",
  clear: "",
  limit: "100",
};

const heatmapFilterState = {
  signal_type: "",
  frequency_hz: "",
  tolerance_hz: "5000000",
  limit: "250",
};

document.getElementById("backend-url").textContent = `Backend: ${backendHttpBase}`;

function formatValue(value, suffix = "") {
  if (value === null || value === undefined || value === "") {
    return "n/a";
  }
  return `${value}${suffix}`;
}

function formatTimestamp(unixSeconds) {
  if (!unixSeconds) {
    return "n/a";
  }
  return new Date(unixSeconds * 1000).toLocaleString();
}

function coords(lat, lon) {
  if (lat === null || lat === undefined || lon === null || lon === undefined) {
    return "n/a";
  }
  return `${Number(lat).toFixed(5)}, ${Number(lon).toFixed(5)}`;
}

async function fetchJson(path, params = null, options = {}) {
  const url = new URL(`${backendHttpBase}${path}`);
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value !== "" && value !== null && value !== undefined) {
        url.searchParams.set(key, value);
      }
    });
  }
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const isJson = response.headers.get("content-type")?.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();
  if (!response.ok) {
    throw new Error(typeof payload === "string" ? payload : JSON.stringify(payload));
  }
  return payload;
}

function writeResult(id, payload) {
  document.getElementById(id).textContent =
    typeof payload === "string" ? payload : JSON.stringify(payload, null, 2);
}

function renderTableRows(id, rows, emptyMessage) {
  const tbody = document.getElementById(id);
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="5">${emptyMessage}</td></tr>`;
    return;
  }
  tbody.innerHTML = rows.join("");
}

async function refreshStatus() {
  const [status, health] = await Promise.all([
    fetchJson("/api/v1/config/status"),
    fetchJson("/api/v1/health"),
  ]);
  const metrics = [
    ["Health", health.status],
    ["Architecture", health.architecture],
    ["Headless", String(health.headless)],
    ["Cloud", String(health.cloud_processing)],
    ["CPU temp", formatValue(status.cpu_temp_c, "°C")],
    ["CPU load", formatValue(status.cpu_load_pct, "%")],
    ["NPU load", formatValue(status.npu_load_pct, "%")],
    ["RAM disk", status.ramdisk_ready ? "ready" : "missing"],
    [
      "Active profile",
      status.active_profile?.profile || "none",
    ],
    ["Keys loaded", String((status.keys_loaded || []).length)],
  ];
  document.getElementById("status-metrics").innerHTML = metrics
    .map(
      ([label, value]) =>
        `<div><dt>${label}</dt><dd>${value}</dd></div>`,
    )
    .join("");
  renderTableRows(
    "device-table",
    (status.devices || []).map(
      (device) => `<tr>
        <td>${device.name}</td>
        <td class="${device.connected ? "state-ok" : "state-warn"}">${device.connected ? "connected" : "missing"}</td>
        <td>${device.detail}</td>
      </tr>`,
    ),
    "No hardware profiles reported.",
  );
}

async function refreshTranscripts() {
  const payload = await fetchJson("/api/v1/telemetry/transcripts", transcriptFilterState);
  renderTableRows(
    "transcript-table",
    payload.transcripts.map(
      (row) => `<tr>
        <td>${formatTimestamp(row.captured_at)}</td>
        <td>${row.talkgroup || "n/a"}</td>
        <td>${row.encrypted ? "enc" : "clear"} / ${row.decrypted ? "decrypted" : "raw"}</td>
        <td>${row.text}</td>
      </tr>`,
    ),
    "No transcripts matched the current filter.",
  );
}

async function refreshDrones() {
  const drones = await fetchJson("/api/v1/telemetry/drones");
  renderTableRows(
    "drone-table",
    drones.map(
      (drone) => `<tr>
        <td>${drone.serial || drone.mac || "n/a"}</td>
        <td>${drone.protocol}</td>
        <td>${drone.rssi_dbm}</td>
        <td>${coords(drone.drone_lat, drone.drone_lon)}</td>
        <td>${coords(drone.pilot_lat, drone.pilot_lon)}</td>
      </tr>`,
    ),
    "No drone contacts available.",
  );
}

function renderHeatmap(points) {
  const plot = document.getElementById("heatmap-plot");
  const summary = document.getElementById("heatmap-summary");
  if (!points.length) {
    plot.innerHTML = "";
    summary.textContent = "No points loaded.";
    return;
  }

  const lats = points.map((point) => point.lat);
  const lons = points.map((point) => point.lon);
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLon = Math.min(...lons);
  const maxLon = Math.max(...lons);
  const lonSpan = Math.max(0.0001, maxLon - minLon);
  const latSpan = Math.max(0.0001, maxLat - minLat);

  plot.innerHTML = points
    .map((point) => {
      const x = 20 + ((point.lon - minLon) / lonSpan) * 600;
      const y = 20 + (1 - (point.lat - minLat) / latSpan) * 200;
      const radius = 4 + Math.max(0, Math.min(14, point.intensity * 10));
      const alpha = 0.25 + Math.max(0.2, Math.min(0.9, point.intensity));
      const title = `${point.signal_type} @ ${point.frequency_hz}Hz`;
      return `<circle cx="${x}" cy="${y}" r="${radius}" fill="rgba(89,193,255,${alpha})"><title>${title}</title></circle>`;
    })
    .join("");
  summary.textContent = `${points.length} heatmap points spanning lat ${minLat.toFixed(5)}–${maxLat.toFixed(5)} / lon ${minLon.toFixed(5)}–${maxLon.toFixed(5)}.`;
}

async function refreshHeatmap() {
  const payload = await fetchJson("/api/v1/telemetry/heatmap", heatmapFilterState);
  renderHeatmap(payload.points || []);
}

async function postJson(path, body) {
  return fetchJson(path, null, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

function parseOptionalNumber(value) {
  return value === "" ? undefined : Number(value);
}

document.getElementById("system-config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const sweepsText = String(form.get("sweeps") || "").trim();
  const payload = {
    profile: form.get("profile"),
    sweeps: sweepsText ? JSON.parse(sweepsText) : null,
  };
  try {
    writeResult("config-results", await postJson("/api/v1/config/system", payload));
    await refreshStatus();
  } catch (error) {
    writeResult("config-results", `Error: ${error.message}`);
  }
});

document.getElementById("key-config-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    writeResult(
      "config-results",
      await postJson("/api/v1/config/keys", {
        keys: [
          {
            key_id: form.get("key_id"),
            algorithm: form.get("algorithm"),
            key_hex: form.get("key_hex"),
          },
        ],
      }),
    );
    await refreshStatus();
  } catch (error) {
    writeResult("config-results", `Error: ${error.message}`);
  }
});

document.getElementById("transcript-filter-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  Object.assign(transcriptFilterState, Object.fromEntries(form.entries()));
  await refreshTranscripts();
});

document.getElementById("heatmap-filter-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  Object.assign(heatmapFilterState, Object.fromEntries(form.entries()));
  await refreshHeatmap();
});

document.getElementById("cot-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    writeResult(
      "operator-results",
      await postJson("/api/v1/cot", {
        uid: form.get("uid"),
        lat: Number(form.get("lat")),
        lon: Number(form.get("lon")),
        hae: Number(form.get("hae")),
      }),
    );
  } catch (error) {
    writeResult("operator-results", `Error: ${error.message}`);
  }
});

document.getElementById("cuas-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    writeResult(
      "operator-results",
      await postJson("/api/v1/cuas/ingest", {
        source: form.get("source"),
        protocol: form.get("protocol"),
        rssi_dbm: Number(form.get("rssi_dbm")),
        serial: form.get("serial") || undefined,
        drone_lat: parseOptionalNumber(form.get("drone_lat")),
        drone_lon: parseOptionalNumber(form.get("drone_lon")),
      }),
    );
    await refreshDrones();
  } catch (error) {
    writeResult("operator-results", `Error: ${error.message}`);
  }
});

async function refreshAll() {
  try {
    await Promise.all([
      refreshStatus(),
      refreshTranscripts(),
      refreshDrones(),
      refreshHeatmap(),
    ]);
  } catch (error) {
    writeResult("config-results", `Error: ${error.message}`);
  }
}

function appendStreamLog(id, payload) {
  const node = document.getElementById(id);
  const current = node.textContent ? `${node.textContent}\n\n` : "";
  node.textContent = `${current}${payload}`.split("\n\n").slice(-8).join("\n\n");
}

function connectStream({ path, statusId, logId, onBinary }) {
  const statusNode = document.getElementById(statusId);
  const socket = new WebSocket(`${backendWsBase}${path}`);
  socket.binaryType = "arraybuffer";
  socket.addEventListener("open", () => {
    statusNode.textContent = "Connected";
    statusNode.className = "stream-status state-ok";
  });
  socket.addEventListener("close", () => {
    statusNode.textContent = "Disconnected — retrying";
    statusNode.className = "stream-status state-warn";
    window.setTimeout(() => connectStream({ path, statusId, logId, onBinary }), 2000);
  });
  socket.addEventListener("error", () => {
    statusNode.textContent = "Stream error";
    statusNode.className = "stream-status state-bad";
  });
  socket.addEventListener("message", (event) => {
    if (typeof event.data === "string") {
      appendStreamLog(logId, event.data);
      return;
    }
    if (onBinary) {
      onBinary(event.data);
    } else {
      appendStreamLog(logId, `[binary ${event.data.byteLength} bytes]`);
    }
  });
}

connectStream({
  path: "/api/v1/stream/waterfall",
  statusId: "waterfall-status",
  logId: "waterfall-log",
});

connectStream({
  path: "/api/v1/stream/sigint",
  statusId: "sigint-status",
  logId: "sigint-log",
});

connectStream({
  path: "/api/v1/stream/fpv/video",
  statusId: "fpv-status",
  logId: "fpv-log",
  onBinary: (data) => {
    const image = document.getElementById("fpv-frame");
    const url = URL.createObjectURL(new Blob([data]));
    image.onload = () => URL.revokeObjectURL(url);
    image.src = url;
    appendStreamLog("fpv-log", `[frame ${data.byteLength} bytes]`);
  },
});

document
  .querySelector('[data-action="refresh-status"]')
  .addEventListener("click", () => refreshStatus());
document
  .querySelector('[data-action="refresh-transcripts"]')
  .addEventListener("click", () => refreshTranscripts());
document
  .querySelector('[data-action="refresh-drones"]')
  .addEventListener("click", () => refreshDrones());
document
  .querySelector('[data-action="refresh-heatmap"]')
  .addEventListener("click", () => refreshHeatmap());
document.getElementById("refresh-all").addEventListener("click", () => refreshAll());

refreshAll();
window.setInterval(refreshStatus, 15000);
window.setInterval(refreshDrones, 10000);
