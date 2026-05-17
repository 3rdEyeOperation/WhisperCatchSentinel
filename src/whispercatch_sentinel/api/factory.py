"""Top-level FastAPI app composition."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..config import RuntimeConfig, is_tmpfs_ramdisk
from ..cot import CotGateway, build_cot_event, multicast_cot
from ..cuas import CuasAggregator, DroneContact
from ..gps import GpsReceiver
from ..heatmap import HeatmapEngine
from ..keys import VolatileKeyVault
from ..storage import Storage
from ..streams import StreamBus
from ..system import collect_status
from .schemas import (
    DroneTelemetry,
    GpsFixResponse,
    HeatmapObservationRequest,
    HeatmapResponse,
    KeyInjectionRequest,
    KeyInjectionResponse,
    SystemConfigRequest,
)


@dataclass
class AppDependencies:
    """Wiring container so tests can inject fakes."""

    config: RuntimeConfig
    storage: Storage
    vault: VolatileKeyVault
    aggregator: CuasAggregator
    heatmap: HeatmapEngine
    bus: StreamBus
    gateway: CotGateway
    # ``gps`` is optional so unit tests and air-gapped dev boxes don't need
    # gpsd running. When present, the API serves the live fix and uses it as
    # the default location for ``POST /api/v1/telemetry/observation``.
    gps: GpsReceiver | None = None


def _build_default_dependencies(
    *,
    storage_path: str | Path = ":memory:",
    vault_path: str | Path | None = None,
    enforce_tmpfs: bool = False,
) -> AppDependencies:
    config = RuntimeConfig()
    storage = Storage(storage_path)
    vault = VolatileKeyVault(
        vault_path or "/dev/shm/whispercatch/keys.json",
        enforce_tmpfs=enforce_tmpfs,
    )
    aggregator = CuasAggregator()
    heatmap = HeatmapEngine(storage)
    bus = StreamBus()
    gateway = CotGateway(config.cot_multicast_group, config.cot_multicast_port)
    gps = (
        GpsReceiver(host=config.gpsd_host, port=config.gpsd_port)
        if config.gpsd_enabled
        else None
    )
    return AppDependencies(
        config=config,
        storage=storage,
        vault=vault,
        aggregator=aggregator,
        heatmap=heatmap,
        bus=bus,
        gateway=gateway,
        gps=gps,
    )


def _dashboard_allowed_origins() -> list[str]:
    configured = os.getenv("WHISPERCATCH_DASHBOARD_ORIGINS")
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]
    return ["http://127.0.0.1:8080", "http://localhost:8080"]


def create_app(deps: AppDependencies | None = None) -> FastAPI:
    deps = deps or _build_default_dependencies()
    app = FastAPI(title="WhisperCatch Sentinel", version="0.2.0")
    app.state.deps = deps
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_dashboard_allowed_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Configuration & control endpoints
    # ------------------------------------------------------------------
    @app.post("/api/v1/config/system")
    def set_system_profile(payload: SystemConfigRequest) -> dict[str, Any]:
        snapshot = {
            "profile": payload.profile,
            "sweeps": payload.sweeps or {},
        }
        deps.storage.set_config("system_profile", snapshot, time.time())
        return {"status": "ok", "applied": snapshot}

    @app.post("/api/v1/config/keys", response_model=KeyInjectionResponse)
    def inject_keys(payload: KeyInjectionRequest) -> KeyInjectionResponse:
        accepted: list[str] = []
        refused: list[dict[str, Any]] = []
        for entry in payload.keys:
            try:
                meta = deps.vault.inject(entry.key_id, entry.algorithm, entry.key_hex)
            except ValueError as exc:
                refused.append({"key_id": entry.key_id, "reason": str(exc)})
            else:
                accepted.append(meta.key_id)
        return KeyInjectionResponse(accepted=accepted, refused=refused)

    @app.get("/api/v1/config/status")
    def system_status() -> dict[str, Any]:
        ramdisk_ready = is_tmpfs_ramdisk(deps.config.tmpfs_path)
        body = collect_status(ramdisk_ready=ramdisk_ready)
        body["keys_loaded"] = [meta.key_id for meta in deps.vault.list_metadata()]
        body["active_profile"] = deps.storage.get_config("system_profile")
        # Surface the gpsd-backed sensor position so the dashboard can show
        # an "anchor" marker and operators can tell at a glance whether the
        # node is geo-aware right now.
        gps_fix = deps.gps.latest_fix() if deps.gps is not None else None
        body["gps"] = {
            "enabled": deps.gps is not None,
            "running": bool(deps.gps and deps.gps.running),
            "host": deps.config.gpsd_host,
            "port": deps.config.gpsd_port,
            "has_fix": gps_fix is not None,
            "fix": gps_fix.to_dict() if gps_fix is not None else None,
        }
        return body

    # ------------------------------------------------------------------
    # Telemetry endpoints
    # ------------------------------------------------------------------
    @app.get("/api/v1/telemetry/drones", response_model=list[DroneTelemetry])
    def list_drones() -> list[DroneTelemetry]:
        deps.aggregator.prune()
        return [
            DroneTelemetry(
                captured_at=c.captured_at,
                source=c.source,
                protocol=c.protocol,
                rssi_dbm=c.rssi_dbm,
                airframe=c.airframe,
                serial=c.serial,
                mac=c.mac,
                drone_lat=c.drone_lat,
                drone_lon=c.drone_lon,
                drone_alt_m=c.drone_alt_m,
                pilot_lat=c.pilot_lat,
                pilot_lon=c.pilot_lon,
                home_lat=c.home_lat,
                home_lon=c.home_lon,
            )
            for c in deps.aggregator.snapshot()
        ]

    @app.get("/api/v1/telemetry/transcripts")
    def list_transcripts(
        talkgroup: str | None = Query(default=None),
        decrypted: bool | None = Query(default=None),
        clear: bool | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> dict[str, Any]:
        rows = deps.storage.query_transcripts(
            talkgroup=talkgroup,
            decrypted=decrypted,
            clear=clear,
            limit=limit,
        )
        return {"count": len(rows), "transcripts": rows}

    @app.get("/api/v1/telemetry/heatmap", response_model=HeatmapResponse)
    def telemetry_heatmap(
        signal_type: str | None = Query(default=None),
        frequency_hz: float | None = Query(default=None, ge=0),
        tolerance_hz: float = Query(default=5_000_000.0, ge=0),
        limit: int = Query(default=5000, ge=1, le=20000),
    ) -> HeatmapResponse:
        rows = deps.heatmap.query(
            signal_type=signal_type,
            frequency_hz=frequency_hz,
            tolerance_hz=tolerance_hz,
            limit=limit,
        )
        return HeatmapResponse(count=len(rows), points=rows)

    @app.get("/api/v1/telemetry/position")
    def telemetry_position() -> GpsFixResponse | None:
        """Return the latest gpsd fix, or ``null`` when no fix is available.

        The dashboard polls this to plot the sensor anchor marker on the
        heatmap so the operator can visualise the receiver's position.
        """
        if deps.gps is None:
            return None
        fix = deps.gps.latest_fix()
        if fix is None:
            return None
        return GpsFixResponse(**fix.to_dict())

    @app.post("/api/v1/telemetry/observation")
    def record_observation(payload: HeatmapObservationRequest) -> dict[str, Any]:
        """Record a heatmap observation, auto-stamping with the live gpsd fix.

        Either ``sensor_lat`` + ``sensor_lon`` must be supplied explicitly,
        or gpsd must be running with a current fix. We never silently fall
        back to (0, 0) — a bad geo-stamp would corrupt every downstream
        heatmap query, so we 409 instead.
        """
        sensor_lat = payload.sensor_lat
        sensor_lon = payload.sensor_lon
        used_gps = False
        if sensor_lat is None or sensor_lon is None:
            fix = deps.gps.latest_fix() if deps.gps is not None else None
            if fix is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "no sensor position available: supply sensor_lat/"
                        "sensor_lon, or enable gpsd and wait for a fix"
                    ),
                )
            sensor_lat = fix.lat
            sensor_lon = fix.lon
            used_gps = True
        ring = deps.heatmap.record(
            sensor_lat=sensor_lat,
            sensor_lon=sensor_lon,
            frequency_hz=payload.frequency_hz,
            rssi_dbm=payload.rssi_dbm,
            signal_type=payload.signal_type,
            tx_power_dbm=payload.tx_power_dbm,
            ring_samples=payload.ring_samples,
            captured_at=payload.captured_at,
        )
        return {
            "status": "ok",
            "points_recorded": len(ring),
            "sensor_lat": sensor_lat,
            "sensor_lon": sensor_lon,
            "source": "gpsd" if used_gps else "request",
        }

    # ------------------------------------------------------------------
    # CoT gateway helper (one-shot POST)
    # ------------------------------------------------------------------
    @app.post("/api/v1/cot")
    def emit_cot(track: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = build_cot_event(
                uid=str(track["uid"]),
                lat=float(track["lat"]),
                lon=float(track["lon"]),
                hae=float(track.get("hae", 0.0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"invalid track: {exc}") from exc
        try:
            multicast_cot(
                deps.config.cot_multicast_group,
                deps.config.cot_multicast_port,
                payload,
            )
        except OSError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"cot_multicast_error:{type(exc).__name__}",
            ) from exc
        return {"status": "sent", "uid": track["uid"]}

    # ------------------------------------------------------------------
    # Legacy/health
    # ------------------------------------------------------------------
    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "headless": deps.config.headless,
            "cloud_processing": deps.config.cloud_processing,
            "architecture": deps.config.architecture,
        }

    # ------------------------------------------------------------------
    # WebSocket streams
    # ------------------------------------------------------------------
    @app.websocket("/api/v1/stream/waterfall")
    async def stream_waterfall(ws: WebSocket) -> None:
        await ws.accept()
        try:
            async with deps.bus.subscribe("waterfall") as queue:
                while True:
                    payload = await queue.get()
                    await ws.send_json(payload)
        except WebSocketDisconnect:
            return

    @app.websocket("/api/v1/stream/sigint")
    async def stream_sigint(ws: WebSocket) -> None:
        await ws.accept()
        try:
            async with deps.bus.subscribe("sigint") as queue:
                while True:
                    payload = await queue.get()
                    await ws.send_json(payload)
        except WebSocketDisconnect:
            return

    @app.websocket("/api/v1/stream/fpv/video")
    async def stream_fpv_video(ws: WebSocket) -> None:
        await ws.accept()
        try:
            async with deps.bus.subscribe("fpv_video") as queue:
                while True:
                    frame = await queue.get()
                    if isinstance(frame, (bytes, bytearray)):
                        await ws.send_bytes(bytes(frame))
                    else:
                        await ws.send_json(frame)
        except WebSocketDisconnect:
            return

    # ------------------------------------------------------------------
    # Test/operator helper for injecting drone contacts (also triggers CoT)
    # ------------------------------------------------------------------
    @app.post("/api/v1/cuas/ingest")
    async def ingest_drone(contact: dict[str, Any]) -> dict[str, Any]:
        try:
            dc = DroneContact(
                captured_at=float(contact.get("captured_at", time.time())),
                source=str(contact["source"]),
                protocol=str(contact["protocol"]),
                rssi_dbm=float(contact["rssi_dbm"]),
                airframe=contact.get("airframe"),
                serial=contact.get("serial"),
                mac=contact.get("mac"),
                uid=contact.get("uid"),
                drone_lat=contact.get("drone_lat"),
                drone_lon=contact.get("drone_lon"),
                drone_alt_m=contact.get("drone_alt_m"),
                pilot_lat=contact.get("pilot_lat"),
                pilot_lon=contact.get("pilot_lon"),
                home_lat=contact.get("home_lat"),
                home_lon=contact.get("home_lon"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"invalid contact: {exc}") from exc
        merged = deps.aggregator.ingest(dc)
        events: list[str] = []
        try:
            events = deps.gateway.broadcast(merged)
        except OSError:
            events = []
        # Fan out to ATAK sigint stream subscribers without blocking the
        # REST response. ``publish`` drops on slow consumers by design.
        await deps.bus.publish("sigint", {"type": "drone", "contact": merged.__dict__})
        return {"status": "ok", "cot_events_sent": len(events)}

    @app.exception_handler(ValueError)
    async def _value_error_handler(_, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app
