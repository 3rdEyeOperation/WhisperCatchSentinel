const dashboardConfig = JSON.parse(
  document.getElementById("dashboard-config").textContent,
);
const backendBaseUrl = dashboardConfig.backendBaseUrl.replace(/\/$/, "");
const backendHttpBase = backendBaseUrl;
const backendWsBase = backendHttpBase.replace(/^http/i, "ws");

const transcript_filter_state = {
  talkgroup: "",
  decrypted: "",
  clear: "",
  limit: "100",
};

const heatmap_filter_state = {
  signal_type: "",
  frequency_hz: "",
  tolerance_hz: "5000000",
  limit: "2000",
};

const band_filter_state = {
  bands: new Set(),
  signal_types: new Set(),
};

const RF_BANDS = [
  { id: "HF", label: "HF", min: 3e6, max: 30e6 },
  { id: "VHF", label: "VHF", min: 30e6, max: 300e6 },
  { id: "UHF", label: "UHF", min: 300e6, max: 1e9 },
  { id: "L", label: "L (1–2 GHz)", min: 1e9, max: 2e9 },
  { id: "ISM-2.4", label: "2.4 GHz ISM", min: 2.4e9, max: 2.5e9 },
  { id: "ISM-915", label: "915 MHz", min: 902e6, max: 928e6 },
  { id: "ISM-433", label: "433 MHz", min: 433e6, max: 435e6 },
  { id: "S", label: "S (2–4 GHz)", min: 2e9, max: 4e9 },
  { id: "C", label: "C (4–6 GHz)", min: 4e9, max: 6e9 },
  { id: "ISM-5.8", label: "5.8 GHz ISM", min: 5.725e9, max: 5.875e9 },
  { id: "X+", label: "X+ (>6 GHz)", min: 6e9, max: 40e9 },
];

const SIGNAL_TYPE_COLORS = {
  analog_fpv: "#ff7b7b",
  droneid: "#ffcc66",
  wifi: "#59c1ff",
  ble: "#a78bfa",
  p25: "#48d597",
  dmr: "#22d3ee",
  unknown: "#9ab0c9",
};

function bandFor(frequency_hz) {
  if (!frequency_hz || frequency_hz <= 0) return { id: "unknown", label: "Unknown" };
  for (const band of RF_BANDS) {
    if (frequency_hz >= band.min && frequency_hz <= band.max) {
      return band;
    }
  }
  return { id: "unknown", label: "Unknown" };
}

function colorFor(signal_type) {
  if (!signal_type) return SIGNAL_TYPE_COLORS.unknown;
  const key = String(signal_type).toLowerCase();
  return SIGNAL_TYPE_COLORS[key] || SIGNAL_TYPE_COLORS.unknown;
}

function formatHz(hz) {
  if (hz === null || hz === undefined) return "n/a";
  const value = Number(hz);
  if (value >= 1e9) return `${(value / 1e9).toFixed(3)} GHz`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(3)} MHz`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(2)} kHz`;
  return `${value} Hz`;
}

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

function renderTableRows(id, rows, emptyMessage, colspan = 1) {
  const tbody = document.getElementById(id);
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="${colspan}">${emptyMessage}</td></tr>`;
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
    3,
  );
}

async function refreshTranscripts() {
  const payload = await fetchJson("/api/v1/telemetry/transcripts", transcript_filter_state);
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
    4,
  );
}

async function refreshDrones() {
  const drones = await fetchJson("/api/v1/telemetry/drones");
  last_drones = drones;
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
    5,
  );
  // Refresh the drone-side planning panel against current heatmap cache.
  if (rf_last_points.length) renderDronePlanning(applyClientFilter(rf_last_points), last_drones);
}

let rf_map = null;
let rf_heat_layer = null;
let rf_marker_layer = null;
let rf_last_points = [];
let last_drones = [];

// View-mode state. "2d" uses the vendored Leaflet map (offline-safe default),
// "3d" lazy-loads Cesium.js from the official CDN for a richer 3D heatmap.
let rf_view_mode = "2d";
let cesium_viewer = null;
let cesium_loading = null;
let cesium_failed = false;
const CESIUM_VERSION = "1.118.2";
const CESIUM_BASE = `https://cdn.jsdelivr.net/npm/cesium@${CESIUM_VERSION}/Build/Cesium`;
// Camera framing constants for the 3D heatmap view.
const CESIUM_MIN_CAMERA_PAD_DEG = 0.001;
const CESIUM_CAMERA_PAD_RATIO = 0.4;

function setViewStatus(message, level = "info") {
  const node = document.getElementById("rf-view-status");
  if (!node) return;
  node.textContent = message || "";
  node.classList.remove("state-warn", "state-bad", "state-ok");
  if (level === "warn") node.classList.add("state-warn");
  else if (level === "bad") node.classList.add("state-bad");
  else if (level === "ok") node.classList.add("state-ok");
}

function loadCesium() {
  if (typeof Cesium !== "undefined") return Promise.resolve(window.Cesium);
  if (cesium_loading) return cesium_loading;
  setViewStatus("Loading Cesium from CDN…");
  cesium_loading = new Promise((resolve, reject) => {
    // Cesium needs CESIUM_BASE_URL to locate its workers, assets and widgets.
    window.CESIUM_BASE_URL = `${CESIUM_BASE}/`;
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = `${CESIUM_BASE}/Widgets/widgets.css`;
    document.head.appendChild(css);
    const script = document.createElement("script");
    script.src = `${CESIUM_BASE}/Cesium.js`;
    script.async = true;
    script.onload = () => resolve(window.Cesium);
    script.onerror = () => reject(new Error("Cesium failed to load (CDN unreachable?)"));
    document.head.appendChild(script);
  }).catch((error) => {
    cesium_loading = null;
    cesium_failed = true;
    throw error;
  });
  return cesium_loading;
}

function ensureCesium() {
  if (cesium_viewer) return cesium_viewer;
  const node = document.getElementById("heatmap-cesium");
  if (!node || typeof Cesium === "undefined") return null;
  // Suppress Ion access-token requirement by clearing it; we use OSM imagery.
  try { Cesium.Ion.defaultAccessToken = ""; } catch (error) { /* ignore */ }
  cesium_viewer = new Cesium.Viewer(node, {
    baseLayerPicker: false,
    geocoder: false,
    timeline: false,
    animation: false,
    homeButton: false,
    navigationHelpButton: false,
    sceneModePicker: false,
    fullscreenButton: false,
    infoBox: true,
    selectionIndicator: true,
    imageryProvider: new Cesium.UrlTemplateImageryProvider({
      url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      subdomains: ["a", "b", "c"],
      maximumLevel: 19,
      credit: "© OpenStreetMap contributors",
    }),
  });
  cesium_viewer.scene.globe.enableLighting = false;
  return cesium_viewer;
}

function cesiumColorFor(signal_type, intensity) {
  const css = colorFor(signal_type);
  // Convert CSS hex/rgb to a Cesium.Color and apply alpha from intensity.
  const color = Cesium.Color.fromCssColorString(css);
  return color.withAlpha(0.45 + Math.min(0.5, Math.max(0, intensity * 0.55)));
}

function renderHeatmapCesium(points) {
  const viewer = ensureCesium();
  if (!viewer) return;
  viewer.entities.removeAll();
  if (!points.length) {
    setViewStatus("3D globe ready — no points in current filter.", "warn");
    return;
  }
  let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
  for (const point of points) {
    const intensity = Math.max(0.05, Math.min(1, point.intensity || 0.2));
    const height = 40 + intensity * 4000; // metres above ellipsoid
    const radius = 60 + intensity * 220;
    viewer.entities.add({
      name: `${point.signal_type || "unknown"} @ ${formatHz(point.frequency_hz)}`,
      position: Cesium.Cartesian3.fromDegrees(point.lon, point.lat, height / 2),
      cylinder: {
        length: height,
        topRadius: radius,
        bottomRadius: radius,
        material: cesiumColorFor(point.signal_type, intensity),
        outline: true,
        outlineColor: Cesium.Color.fromCssColorString(colorFor(point.signal_type)).withAlpha(0.85),
      },
      description:
        `<table style="color:#eaf2ff">
          <tr><th>Signal</th><td>${point.signal_type || "unknown"}</td></tr>
          <tr><th>Frequency</th><td>${formatHz(point.frequency_hz)}</td></tr>
          <tr><th>RSSI</th><td>${point.rssi_dbm} dBm</td></tr>
          <tr><th>Intensity</th><td>${intensity.toFixed(2)}</td></tr>
          <tr><th>Captured</th><td>${formatTimestamp(point.captured_at)}</td></tr>
        </table>`,
    });
    minLat = Math.min(minLat, point.lat);
    maxLat = Math.max(maxLat, point.lat);
    minLon = Math.min(minLon, point.lon);
    maxLon = Math.max(maxLon, point.lon);
  }
  // Frame the data set with a small padding ring.
  const padLat = Math.max(CESIUM_MIN_CAMERA_PAD_DEG, (maxLat - minLat) * CESIUM_CAMERA_PAD_RATIO);
  const padLon = Math.max(CESIUM_MIN_CAMERA_PAD_DEG, (maxLon - minLon) * CESIUM_CAMERA_PAD_RATIO);
  viewer.camera.flyTo({
    destination: Cesium.Rectangle.fromDegrees(
      minLon - padLon,
      minLat - padLat,
      maxLon + padLon,
      maxLat + padLat,
    ),
    duration: 0,
  });
  setViewStatus(`3D globe rendering ${points.length} emitter columns.`, "ok");
}

async function switchView(mode) {
  if (mode === rf_view_mode) return;
  rf_view_mode = mode;
  document.querySelectorAll(".view-toggle-btn").forEach((btn) => {
    const active = btn.dataset.view === mode;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", String(active));
  });
  const map2d = document.getElementById("heatmap-map");
  const cesiumNode = document.getElementById("heatmap-cesium");
  const fallback = document.getElementById("heatmap-fallback");
  if (mode === "3d") {
    map2d?.classList.add("hidden");
    fallback?.classList.add("hidden");
    cesiumNode?.classList.remove("hidden");
    try {
      await loadCesium();
      renderHeatmapCesium(applyClientFilter(rf_last_points));
    } catch (error) {
      setViewStatus(`${error.message} — staying on 2D map.`, "bad");
      // Roll back to 2D so the operator is not left with a blank pane.
      await switchView("2d");
    }
  } else {
    cesiumNode?.classList.add("hidden");
    map2d?.classList.remove("hidden");
    // Leaflet needs a size hint after being un-hidden.
    if (rf_map) setTimeout(() => rf_map.invalidateSize(), 50);
    renderHeatmap(rf_last_points);
    if (!cesium_failed) setViewStatus("");
  }
}

function ensureMap() {
  if (rf_map || typeof L === "undefined") return rf_map;
  const node = document.getElementById("heatmap-map");
  if (!node) return null;
  document.getElementById("heatmap-fallback")?.classList.add("hidden");
  rf_map = L.map(node, {
    center: [20, 0],
    zoom: 2,
    worldCopyJump: true,
    preferCanvas: true,
  });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(rf_map);
  rf_marker_layer = L.layerGroup().addTo(rf_map);
  if (L.heatLayer) {
    rf_heat_layer = L.heatLayer([], {
      radius: 28,
      blur: 24,
      minOpacity: 0.35,
      maxZoom: 17,
      gradient: {
        0.0: "#0a3b66",
        0.25: "#1f8fff",
        0.5: "#48d597",
        0.7: "#ffcc66",
        1.0: "#ff5252",
      },
    }).addTo(rf_map);
  }
  return rf_map;
}

function applyClientFilter(points) {
  return points.filter((point) => {
    if (band_filter_state.bands.size) {
      const band = bandFor(point.frequency_hz);
      if (!band_filter_state.bands.has(band.id)) return false;
    }
    if (band_filter_state.signal_types.size) {
      if (!band_filter_state.signal_types.has(String(point.signal_type).toLowerCase())) {
        return false;
      }
    }
    return true;
  });
}

function renderHeatmapMap(points) {
  const map = ensureMap();
  if (!map) {
    renderHeatmapFallback(points);
    return;
  }
  rf_marker_layer.clearLayers();
  const heatPoints = points.map((point) => [point.lat, point.lon, Math.max(0.05, Math.min(1, point.intensity || 0.2))]);
  if (rf_heat_layer) rf_heat_layer.setLatLngs(heatPoints);

  for (const point of points) {
    const radius = 3 + Math.max(0, Math.min(8, (point.intensity || 0) * 8));
    L.circleMarker([point.lat, point.lon], {
      radius,
      color: colorFor(point.signal_type),
      weight: 1,
      fillColor: colorFor(point.signal_type),
      fillOpacity: 0.65,
    })
      .bindPopup(
        `<strong>${point.signal_type || "unknown"}</strong><br>${formatHz(point.frequency_hz)}<br>RSSI ${point.rssi_dbm} dBm<br>${formatTimestamp(point.captured_at)}`,
      )
      .addTo(rf_marker_layer);
  }

  if (points.length) {
    const bounds = L.latLngBounds(points.map((p) => [p.lat, p.lon]));
    map.fitBounds(bounds.pad(0.2), { maxZoom: 14, animate: false });
  }
}

function renderHeatmapFallback(points) {
  const plot = document.getElementById("heatmap-fallback");
  if (!plot) return;
  plot.classList.remove("hidden");
  if (!points.length) {
    plot.innerHTML = "";
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
      const radius = 4 + Math.max(0, Math.min(14, (point.intensity || 0) * 10));
      const alpha = 0.25 + Math.max(0.2, Math.min(0.9, point.intensity || 0));
      const color = colorFor(point.signal_type);
      const title = `${point.signal_type} @ ${formatHz(point.frequency_hz)}`;
      return `<circle cx="${x}" cy="${y}" r="${radius}" fill="${color}" fill-opacity="${alpha}"><title>${title}</title></circle>`;
    })
    .join("");
}

function renderBandChips(points) {
  const counts = new Map();
  for (const point of points) {
    const band = bandFor(point.frequency_hz);
    counts.set(band.id, (counts.get(band.id) || 0) + 1);
  }
  const node = document.getElementById("band-chips");
  if (!node) return;
  const chips = RF_BANDS.filter((band) => counts.has(band.id)).map((band) => {
    const active = band_filter_state.bands.has(band.id);
    return `<button type="button" class="chip ${active ? "chip-active" : ""}" data-band="${band.id}">${band.label} <span>${counts.get(band.id)}</span></button>`;
  });
  if (counts.has("unknown")) {
    const active = band_filter_state.bands.has("unknown");
    chips.push(`<button type="button" class="chip ${active ? "chip-active" : ""}" data-band="unknown">Unknown <span>${counts.get("unknown")}</span></button>`);
  }
  node.innerHTML = chips.join("") || `<span class="hint">No bands in current query.</span>`;
}

function renderSignalChips(points) {
  const counts = new Map();
  for (const point of points) {
    const key = String(point.signal_type || "unknown").toLowerCase();
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const node = document.getElementById("signal-chips");
  if (!node) return;
  const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  node.innerHTML = entries
    .map(([type, count]) => {
      const active = band_filter_state.signal_types.has(type);
      return `<button type="button" class="chip ${active ? "chip-active" : ""}" data-signal="${type}" style="--chip-accent:${colorFor(type)}">${type} <span>${count}</span></button>`;
    })
    .join("") || `<span class="hint">No signal types in current query.</span>`;
}

function aggregateBands(points) {
  const buckets = new Map();
  for (const point of points) {
    const band = bandFor(point.frequency_hz);
    const entry = buckets.get(band.id) || {
      id: band.id,
      label: band.label,
      hits: 0,
      sumRssi: 0,
      peak: -Infinity,
    };
    entry.hits += 1;
    entry.sumRssi += Number(point.rssi_dbm) || 0;
    entry.peak = Math.max(entry.peak, Number(point.rssi_dbm) || entry.peak);
    buckets.set(band.id, entry);
  }
  return [...buckets.values()]
    .map((entry) => ({
      ...entry,
      avgRssi: entry.hits ? entry.sumRssi / entry.hits : 0,
      peak: entry.peak === -Infinity ? null : entry.peak,
    }))
    .sort((a, b) => b.hits - a.hits);
}

function aggregateEmitters(points) {
  // Quantize frequencies to 1 MHz bins so close hits collapse into one emitter row.
  const buckets = new Map();
  for (const point of points) {
    const freq = Number(point.frequency_hz) || 0;
    const key = `${Math.round(freq / 1e6)}|${String(point.signal_type || "unknown").toLowerCase()}`;
    const entry = buckets.get(key) || {
      frequency_hz: freq,
      signal_type: point.signal_type || "unknown",
      hits: 0,
      sumRssi: 0,
    };
    entry.hits += 1;
    entry.sumRssi += Number(point.rssi_dbm) || 0;
    buckets.set(key, entry);
  }
  return [...buckets.values()]
    .map((entry) => ({ ...entry, avgRssi: entry.hits ? entry.sumRssi / entry.hits : 0 }))
    .sort((a, b) => b.hits - a.hits)
    .slice(0, 8);
}

function commPlanSuggestions(bands) {
  // Recommend lowest-activity bands among classic comm bands as friendly channels.
  const commCandidates = new Set(["HF", "VHF", "UHF", "ISM-433", "ISM-915", "ISM-2.4", "ISM-5.8"]);
  const ranked = bands
    .filter((band) => commCandidates.has(band.id))
    .sort((a, b) => a.hits - b.hits);
  if (!ranked.length) {
    return ["No comm-band data yet — load more spectrum samples."];
  }
  return ranked.slice(0, 4).map((band) => {
    const verdict = band.hits < 4 ? "quiet — good candidate" : band.hits < 12 ? "moderate" : "congested";
    return `<strong>${band.label}</strong>: ${band.hits} hits, avg ${band.avgRssi.toFixed(1)} dBm — <em>${verdict}</em>`;
  });
}

function renderRfSummary(points, bands) {
  const node = document.getElementById("rf-summary");
  if (!node) return;
  const total = points.length;
  const uniqueTypes = new Set(points.map((p) => String(p.signal_type || "unknown").toLowerCase()));
  const strongest = points.reduce(
    (acc, point) => (point.rssi_dbm > acc ? point.rssi_dbm : acc),
    -Infinity,
  );
  const dominantBand = bands[0]?.label || "n/a";
  const items = [
    ["Total samples", total],
    ["Signal types", uniqueTypes.size],
    ["Bands", bands.length],
    ["Strongest", strongest === -Infinity ? "n/a" : `${strongest.toFixed(1)} dBm`],
    ["Dominant band", dominantBand],
  ];
  node.innerHTML = items
    .map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`)
    .join("");
}

function renderBandBreakdown(bands) {
  renderTableRows(
    "band-breakdown",
    bands.map(
      (band) => `<tr>
        <td>${band.label}</td>
        <td>${band.hits}</td>
        <td>${band.avgRssi.toFixed(1)} dBm</td>
        <td>${band.peak === null ? "n/a" : band.peak.toFixed(1) + " dBm"}</td>
      </tr>`,
    ),
    "No band activity in current query.",
    4,
  );
}

function renderTopEmitters(emitters) {
  renderTableRows(
    "top-emitters",
    emitters.map(
      (entry) => `<tr>
        <td>${formatHz(entry.frequency_hz)}</td>
        <td><span class="dot" style="background:${colorFor(entry.signal_type)}"></span>${entry.signal_type}</td>
        <td>${entry.hits}</td>
        <td>${entry.avgRssi.toFixed(1)} dBm</td>
      </tr>`,
    ),
    "No emitter clusters yet.",
    4,
  );
}

function renderCommSuggestions(bands) {
  const node = document.getElementById("comm-suggestions");
  if (!node) return;
  const items = commPlanSuggestions(bands);
  node.innerHTML = items.map((item) => `<li>${item}</li>`).join("");
}

function renderDronePlanning(points, drones) {
  const protocolBuckets = new Map();
  for (const drone of drones) {
    const protocol = String(drone.protocol || "unknown");
    const entry = protocolBuckets.get(protocol) || {
      protocol,
      drones: new Set(),
      sumRssi: 0,
      count: 0,
      bands: new Set(),
    };
    entry.drones.add(drone.serial || drone.mac || drone.uid || Math.random().toString(36));
    entry.sumRssi += Number(drone.rssi_dbm) || 0;
    entry.count += 1;
    // Map drone source to spectrum bands using the protocol's typical RF band heuristics.
    if (/ocusync|droneid/i.test(protocol)) entry.bands.add("ISM-2.4");
    if (/wifi/i.test(protocol)) entry.bands.add("ISM-2.4");
    if (/ble|bluetooth/i.test(protocol)) entry.bands.add("ISM-2.4");
    if (/fpv|analog/i.test(protocol)) entry.bands.add("ISM-5.8");
    protocolBuckets.set(protocol, entry);
  }

  // Augment with spectrum-side hits for drone-relevant signal types.
  const droneSignals = new Set(["droneid", "wifi", "ble", "analog_fpv"]);
  for (const point of points) {
    const sig = String(point.signal_type || "").toLowerCase();
    if (!droneSignals.has(sig)) continue;
    const protocol = sig;
    const entry = protocolBuckets.get(protocol) || {
      protocol,
      drones: new Set(),
      sumRssi: 0,
      count: 0,
      bands: new Set(),
    };
    entry.bands.add(bandFor(point.frequency_hz).id);
    protocolBuckets.set(protocol, entry);
  }

  const rows = [...protocolBuckets.values()].map((entry) => ({
    ...entry,
    droneCount: entry.drones.size,
    avgRssi: entry.count ? entry.sumRssi / entry.count : null,
  }));
  rows.sort((a, b) => b.droneCount - a.droneCount || b.count - a.count);

  renderTableRows(
    "drone-band-table",
    rows.map(
      (entry) => `<tr>
        <td>${entry.protocol}</td>
        <td>${entry.droneCount}</td>
        <td>${[...entry.bands].join(", ") || "n/a"}</td>
        <td>${entry.avgRssi === null ? "n/a" : entry.avgRssi.toFixed(1) + " dBm"}</td>
      </tr>`,
    ),
    "No drone protocols observed yet.",
    4,
  );

  const node = document.getElementById("drone-suggestions");
  if (!node) return;
  const contestedBands = new Set();
  for (const entry of rows) entry.bands.forEach((b) => contestedBands.add(b));
  if (!contestedBands.size) {
    node.innerHTML = `<li>No contested drone bands detected. Default link plan acceptable.</li>`;
    return;
  }
  const allCommBands = ["ISM-433", "ISM-915", "ISM-2.4", "ISM-5.8"];
  const cleaner = allCommBands.filter((b) => !contestedBands.has(b));
  const tips = [
    `Contested by drone traffic: <strong>${[...contestedBands].join(", ")}</strong>`,
  ];
  if (cleaner.length) {
    tips.push(`Prefer drone telemetry / video links on: <strong>${cleaner.join(", ")}</strong>`);
  } else {
    tips.push(`All common drone bands are contested — consider frequency hopping or licensed bands.`);
  }
  node.innerHTML = tips.map((t) => `<li>${t}</li>`).join("");
}

function renderHeatmap(points) {
  rf_last_points = points;
  const filtered = applyClientFilter(points);
  renderBandChips(points);
  renderSignalChips(points);
  if (rf_view_mode === "3d" && typeof Cesium !== "undefined") {
    renderHeatmapCesium(filtered);
  } else {
    renderHeatmapMap(filtered);
    if (typeof L === "undefined") renderHeatmapFallback(filtered);
  }
  const bands = aggregateBands(filtered);
  renderRfSummary(filtered, bands);
  renderBandBreakdown(bands);
  renderTopEmitters(aggregateEmitters(filtered));
  renderCommSuggestions(bands);
  renderDronePlanning(filtered, last_drones);
}

async function refreshHeatmap() {
  const payload = await fetchJson("/api/v1/telemetry/heatmap", heatmap_filter_state);
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
  Object.assign(transcript_filter_state, Object.fromEntries(form.entries()));
  await refreshTranscripts();
});

document.getElementById("heatmap-filter-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  Object.assign(heatmap_filter_state, Object.fromEntries(form.entries()));
  await refreshHeatmap();
});

document.getElementById("heatmap-reset").addEventListener("click", async () => {
  Object.assign(heatmap_filter_state, {
    signal_type: "",
    frequency_hz: "",
    tolerance_hz: "5000000",
    limit: "2000",
  });
  band_filter_state.bands.clear();
  band_filter_state.signal_types.clear();
  document.getElementById("heatmap-filter-form").reset();
  await refreshHeatmap();
});

document.getElementById("band-chips").addEventListener("click", (event) => {
  const target = event.target.closest("[data-band]");
  if (!target) return;
  const band = target.dataset.band;
  if (band_filter_state.bands.has(band)) band_filter_state.bands.delete(band);
  else band_filter_state.bands.add(band);
  renderHeatmap(rf_last_points);
});

document.getElementById("signal-chips").addEventListener("click", (event) => {
  const target = event.target.closest("[data-signal]");
  if (!target) return;
  const sig = target.dataset.signal;
  if (band_filter_state.signal_types.has(sig)) band_filter_state.signal_types.delete(sig);
  else band_filter_state.signal_types.add(sig);
  renderHeatmap(rf_last_points);
});

document.querySelectorAll(".view-toggle-btn").forEach((btn) => {
  btn.addEventListener("click", () => switchView(btn.dataset.view));
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
