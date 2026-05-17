"""Pydantic schemas for the public REST API surface.

These models are deliberately strict so the ATAK Android Plugin can rely on
their JSON shape. Anything sensitive (key bytes, IVs, raw audio paths) is
*never* declared here — that data stays on the RED side of the boundary.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SYSTEM_PROFILES = {"SCAN_COMBAT", "MONITOR_VOICE", "DRONE_HUNT", "PASSIVE"}

# Valid SDR roles — matches the SdrRole type in config.py.
SDR_ROLES = {"scout", "action", "aux"}


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


class SdrDeviceInfo(BaseModel):
    """Runtime state of one SDR radio including its assigned operator role."""

    name: str
    role: Literal["scout", "action", "aux"]
    purpose: str
    capabilities: list[str]
    connected: bool
    detail: str


class SdrRoleAssignRequest(BaseModel):
    """Reassign an SDR device to a different operator role at runtime."""

    model_config = ConfigDict(extra="forbid")

    device_name: str = Field(min_length=1, max_length=128)
    role: Literal["scout", "action", "aux"]


class SdrRoleAssignResponse(BaseModel):
    status: str
    device_name: str
    role: str


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


class GpsFixResponse(BaseModel):
    """Live sensor location as reported by the on-board gpsd daemon."""

    lat: float
    lon: float
    altitude_m: float | None = None
    mode: int
    received_at: float
    fix_time: str | None = None
    speed_mps: float | None = None
    track_deg: float | None = None
    eph_m: float | None = None
    device: str | None = None


class HeatmapObservationRequest(BaseModel):
    """Operator / sensor task input for recording a heatmap observation.

    ``sensor_lat`` and ``sensor_lon`` are optional: when omitted, the API
    falls back to the live gpsd fix so the sensor doesn't have to know its
    own coordinates. If gpsd has no fix either, the request is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    frequency_hz: float = Field(gt=0)
    rssi_dbm: float
    signal_type: str = Field(min_length=1, max_length=64)
    sensor_lat: float | None = Field(default=None, ge=-90, le=90)
    sensor_lon: float | None = Field(default=None, ge=-180, le=180)
    tx_power_dbm: float = 20.0
    ring_samples: int = Field(default=12, ge=1, le=64)
    captured_at: float | None = None
