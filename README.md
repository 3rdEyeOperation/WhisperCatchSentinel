# WhisperCatchSentinel

Tactical sensor node backend with a FastAPI API surface and a local operator
dashboard. Features multi-channel P25/DMR digital trunking decryption, local
Whisper voice transcription, Direct Remote ID / DJI DroneID tracking, and
5.8GHz analog FPV video interception. Integrates with downstream clients via
REST APIs, WebSocket streams, multicast Cursor-on-Target, and a browser-based
dashboard.

## Architecture

```
hardware                processing                 outputs
--------                ----------                 -------
HackRF One       ─►  spectrum (SDRangel REST)  ─►  fpv MJPEG WS
RTL-SDR V4       ─►  sigint   (TrunkRec+whisper) ► sigint WS / REST
Alfa Wi-Fi       ─►  cuas (droneid aggregator) ─►  drones REST / CoT
Sniffle BLE      ─►  cuas                       ─►  drones REST / CoT
                     heatmap (RSSI + path loss) ►  heatmap REST
                     keys vault (tmpfs only)    ►  decrypt boundary
```

The `whispercatch_sentinel` package implements each block:

| Module       | Responsibility                                                |
|--------------|---------------------------------------------------------------|
| `spectrum`   | SDRangel REST sweeps, analog-FPV classification, ATV spin-up  |
| `cuas`       | DroneID / Wi-Fi / BLE aggregation with dedup by serial/MAC    |
| `sigint`     | Trunk Recorder hand-off, OpenSSL decrypt, whisper.cpp output  |
| `heatmap`    | Friis-based RSSI distance + GroundOverlay-ready point arrays  |
| `gps`        | gpsd JSON client + background fix cache for live sensor coords|
| `cot`        | TAK-schema CoT XML builders + multicast gateway               |
| `keys`       | Volatile tmpfs key vault (RED-side only)                      |
| `storage`    | SQLite persistence for configs / transcripts / heatmap points |
| `streams`    | In-process pub/sub for WebSocket fan-out                      |
| `system`     | CPU/NPU/USB telemetry probes                                  |
| `api`        | FastAPI app composition + routers                             |

## REST + WebSocket endpoints

```
POST  /api/v1/config/system          # set scan profile + frequency sweeps
POST  /api/v1/config/keys            # inject keys into tmpfs vault (never persisted)
GET   /api/v1/config/status          # CPU temp, NPU load, USB devices, ramdisk

GET   /api/v1/telemetry/drones       # live aggregated UAS contacts
GET   /api/v1/telemetry/transcripts  # filterable transcript log
GET   /api/v1/telemetry/heatmap      # spatial RF grid for ATAK overlay

WS    /api/v1/stream/waterfall       # raw FFT frames for spectrum UI
WS    /api/v1/stream/sigint          # transcripts + drone events
WS    /api/v1/stream/fpv/video       # MJPEG / binary FPV frames

POST  /api/v1/cot                    # ad-hoc multicast CoT emission
POST  /api/v1/cuas/ingest            # operator helper for injecting contacts

GET   /api/v1/telemetry/position     # live sensor position (gpsd fix) or null
POST  /api/v1/telemetry/observation  # record a heatmap point — auto-stamps with live gpsd fix
```

All endpoints respond in strict JSON. Cryptographic keys are stored exclusively
in the volatile RAM-disk vault (default `/mnt/ramdisk/keys.json`); no key bytes
are ever returned through the API.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]

# Backend API on http://127.0.0.1:8000
uvicorn whispercatch_sentinel.app:app --host 0.0.0.0 --port 8000

# Dashboard UI on http://127.0.0.1:8080
uvicorn whispercatch_sentinel.dashboard.app:app --host 0.0.0.0 --port 8080
```

The dashboard expects the backend to be reachable at `http://127.0.0.1:8000`
by default. Override this when needed:

```bash
WHISPERCATCH_BACKEND_URL=http://127.0.0.1:8000 \
uvicorn whispercatch_sentinel.dashboard.app:app --host 0.0.0.0 --port 8080
```

The backend accepts browser requests from `http://127.0.0.1:8080` and
`http://localhost:8080` by default. To allow additional dashboard origins, set:

```bash
WHISPERCATCH_DASHBOARD_ORIGINS=http://127.0.0.1:8080,http://localhost:8080
```

## Dashboard features

The dashboard served on port `8080` provides:

- system health and hardware status
- system profile and key injection forms
- transcript viewing with talkgroup/decryption filters
- drone telemetry tables
- **CloudRF-style RF coverage map** with a `2D map` / `3D globe` toggle:
  - **2D map**: vendored Leaflet + heat overlay (works fully offline).
  - **3D globe**: Cesium.js (lazy-loaded from the official CDN) rendering each
    emitter as a colored 3D column whose height encodes intensity and color
    encodes signal type. Falls back to the 2D map with a banner notice if the
    Cesium CDN is unreachable.
  - Band and signal-type filter chips, spectrum summary, band breakdown, top
    emitter list, comm-plan channel suggestions, and a drone-telemetry
    frequency-planning panel.
- live WebSocket panels for waterfall, SIGINT, and FPV streams
- operator helper forms for CoT emission and CUAS ingest

The Leaflet map assets are bundled under
`whispercatch_sentinel/dashboard/static/vendor/leaflet/` so the dashboard works
in air-gapped environments. If the operator browser cannot reach OpenStreetMap
tile servers, the map still renders heat points; a static SVG plot is provided
as a final fallback. Cesium.js (used only when the operator switches to the
**3D globe** view) is loaded on demand from the official jsDelivr CDN — it is
too large (~100 MB unpacked) to vendor; when the CDN is unreachable, the panel
remains on the offline 2D Leaflet view and the operator is notified inline.

## Sensor positioning (gpsd)

The sensor node uses [gpsd](https://gpsd.io/) as the canonical source of its
own location, so every heatmap point can be stamped with the actual antenna
position rather than a hand-typed coordinate.

Bring up gpsd on the sensor host (Raspberry Pi, NUC, …):

```bash
sudo apt install gpsd gpsd-clients
sudo systemctl enable --now gpsd
# Verify with: gpspipe -w -n 5
```

Then enable the integration when constructing the backend dependencies (set
`gpsd_enabled=True` on `RuntimeConfig`) and start the receiver thread once
the API process is up:

```python
deps = _build_default_dependencies()
# In real deployments — call this from your start-up script:
if deps.gps is not None:
    deps.gps.start()
```

Behaviour:

- `GET /api/v1/telemetry/position` returns the latest TPV fix as JSON, or
  `null` when gpsd is disabled, unreachable, or has no fix yet.
- `GET /api/v1/config/status` includes a `gps` block (`enabled`, `running`,
  `has_fix`, `fix`) so the dashboard can show a one-line "Sensor GPS" chip
  and plot a bullseye anchor marker on both the 2D map and the 3D globe.
- `POST /api/v1/telemetry/observation` records a heatmap evidence ring. It
  uses the live gpsd fix as the default `sensor_lat`/`sensor_lon`; operators
  may still pass explicit coordinates to override. If neither is available
  the call is rejected with `409` rather than silently writing rotten
  `(0, 0)` points — corrupting downstream heatmap queries would be worse
  than failing loudly.
- Fixes older than `fix_ttl_s` (default 30 s) are treated as stale and not
  served, so a sensor that loses sky view stops geo-stamping new
  observations instead of drifting.

The integration uses no third-party Python packages — gpsd's line-delimited
JSON protocol is spoken directly over TCP — so the air-gapped sensor image
stays small.

## Test

```bash
pytest
```
