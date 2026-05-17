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
- **CloudRF-style RF coverage map** (Leaflet + heat overlay) with band/signal
  filter chips, spectrum summary, band breakdown, top emitter list, comm-plan
  channel suggestions, and a drone-telemetry frequency-planning panel
- live WebSocket panels for waterfall, SIGINT, and FPV streams
- operator helper forms for CoT emission and CUAS ingest

The Leaflet map assets are bundled under
`whispercatch_sentinel/dashboard/static/vendor/leaflet/` so the dashboard works
in air-gapped environments. If the operator browser cannot reach OpenStreetMap
tile servers, the map still renders heat points; a static SVG plot is provided
as a final fallback.

## Test

```bash
pytest
```
