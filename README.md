# WhisperCatchSentinel

Headless tactical SIGINT & C-UAS sensor node engineered for ARM64 edge devices.
Features multi-channel P25/DMR digital trunking decryption, local Whisper voice
transcription, Direct Remote ID / DJI DroneID tracking, and 5.8GHz analog FPV
video interception. Integrates seamlessly with Iron Patriot mesh networks and
ATAK via REST APIs, WebSocket streams, and multicast Cursor-on-Target.

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
uvicorn whispercatch_sentinel.app:app --host 0.0.0.0 --port 8000
```

## Test

```bash
pytest
```
