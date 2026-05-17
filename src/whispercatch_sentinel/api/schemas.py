"""Pydantic schemas for the public REST API surface.

These models are deliberately strict so the ATAK Android Plugin can rely on
their JSON shape. Anything sensitive (key bytes, IVs, raw audio paths) is
*never* declared here — that data stays on the RED side of the boundary.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SYSTEM_PROFILES = {"SCAN_COMBAT", "MONITOR_VOICE", "DRONE_HUNT", "PASSIVE"}


class SystemConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["SCAN_COMBAT", "MONITOR_VOICE", "DRONE_HUNT", "PASSIVE"]
    sweeps: dict[str, list[int]] | None = Field(
        default=None,
        description=(
            "Optional override map of band-name → [start_hz, stop_hz, step_hz]"
        ),
    )


class KeyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(min_length=1, max_length=64)
    algorithm: Literal["AES-256-OFB", "DES-OFB"]
    key_hex: str = Field(min_length=2, pattern=r"^[0-9a-fA-F]+$")


class KeyInjectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keys: list[KeyEntry]


class KeyInjectionResponse(BaseModel):
    accepted: list[str]
    refused: list[dict]


class DroneTelemetry(BaseModel):
    captured_at: float
    source: str
    protocol: str
    rssi_dbm: float
    airframe: str | None = None
    serial: str | None = None
    mac: str | None = None
    drone_lat: float | None = None
    drone_lon: float | None = None
    drone_alt_m: float | None = None
    pilot_lat: float | None = None
    pilot_lon: float | None = None
    home_lat: float | None = None
    home_lon: float | None = None


class HeatmapPoint(BaseModel):
    lat: float
    lon: float
    intensity: float
    frequency_hz: float
    rssi_dbm: float
    signal_type: str
    captured_at: float


class HeatmapResponse(BaseModel):
    count: int
    points: list[HeatmapPoint]
