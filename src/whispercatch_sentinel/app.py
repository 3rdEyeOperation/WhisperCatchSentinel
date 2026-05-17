from __future__ import annotations

import asyncio

from fastapi import FastAPI, HTTPException, WebSocket
from pydantic import BaseModel

from .config import HARDWARE_PROFILES, RuntimeConfig, detect_device, is_tmpfs_ramdisk
from .cot import build_cot_event, multicast_cot


class CotTrack(BaseModel):
    uid: str
    lat: float
    lon: float
    hae: float = 0.0


def create_app() -> FastAPI:
    app = FastAPI(title="WhisperCatch Sentinel", version="0.1.0")
    config = RuntimeConfig()

    @app.get("/api/v1/health")
    def health() -> dict[str, object]:
        return {
            "status": "ok",
            "headless": config.headless,
            "cloud_processing": config.cloud_processing,
            "architecture": config.architecture,
        }

    @app.get("/api/v1/runtime")
    def runtime() -> dict[str, object]:
        return {
            "config": config.model_dump(),
            "devices": [detect_device(profile).__dict__ for profile in HARDWARE_PROFILES],
            "crypto_ramdisk_ready": is_tmpfs_ramdisk(config.tmpfs_path),
        }

    @app.post("/api/v1/cot")
    def emit_cot(track: CotTrack) -> dict[str, object]:
        payload = build_cot_event(uid=track.uid, lat=track.lat, lon=track.lon, hae=track.hae)
        try:
            multicast_cot(config.cot_multicast_group, config.cot_multicast_port, payload)
        except OSError as exc:
            raise HTTPException(status_code=503, detail=f"cot_multicast_error:{type(exc).__name__}") from exc
        return {"status": "sent", "uid": track.uid}

    @app.websocket("/ws/spectrum")
    async def spectrum(ws: WebSocket) -> None:
        await ws.accept()
        try:
            for i in range(5):
                await ws.send_json({"bin": i, "power_db": -50 + i})
                await asyncio.sleep(0.01)
        finally:
            await ws.close()

    return app


app = create_app()
