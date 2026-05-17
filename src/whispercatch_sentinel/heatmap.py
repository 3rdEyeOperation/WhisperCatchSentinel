"""RF spatial heatmap engine.

Each intercepted signal (voice talkgroup, Remote-ID beacon, unknown FPV
emission, ...) is reduced to a ``HeatmapPoint`` that the ATAK plugin can
project onto a map. A simple free-space-path-loss model is used to derive a
distance ring; the sensor node treats itself as the centre of that ring and
emits a small constellation of synthetic points around the perimeter so a
GroundOverlay rendering looks like a soft RF "halo" around the emitter
locus rather than a single dot.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass

from .storage import HeatmapRecord, Storage


# Distance ring intensity falls off linearly with distance and is clipped
# to [0, 1] so the ATAK overlay always has a stable colour scale.
DEFAULT_MAX_DISTANCE_M = 5_000.0
_EARTH_RADIUS_M = 6_371_000.0


@dataclass(frozen=True)
class HeatmapPoint:
    lat: float
    lon: float
    normalized_intensity: float


def free_space_distance_m(rssi_dbm: float, frequency_hz: float, *, tx_power_dbm: float = 20.0) -> float:
    """Friis free-space distance estimate in metres.

    The Friis equation in dB:
        ``FSPL = 20*log10(d) + 20*log10(f) + 20*log10(4π/c)``

    Returns a bounded distance >= 1m to keep the heatmap renderer sane when
    sensors detect very strong nearby emitters.
    """
    if frequency_hz <= 0:
        raise ValueError("frequency_hz must be positive")
    fspl_db = max(tx_power_dbm - rssi_dbm, 0.0)
    # d = 10 ** ((FSPL - 20*log10(f) - 20*log10(4π/c)) / 20)
    log_term = 20.0 * math.log10(frequency_hz) + 20.0 * math.log10(4.0 * math.pi / 299_792_458.0)
    distance_m = 10.0 ** ((fspl_db - log_term) / 20.0)
    return max(1.0, distance_m)


def normalize_intensity(distance_m: float, *, max_distance_m: float = DEFAULT_MAX_DISTANCE_M) -> float:
    if max_distance_m <= 0:
        raise ValueError("max_distance_m must be positive")
    intensity = 1.0 - (distance_m / max_distance_m)
    return max(0.0, min(1.0, intensity))


def offset_latlon(lat: float, lon: float, distance_m: float, bearing_deg: float) -> tuple[float, float]:
    """Great-circle offset using the spherical earth approximation."""
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)
    bearing_rad = math.radians(bearing_deg)
    angular = distance_m / _EARTH_RADIUS_M

    new_lat = math.asin(
        math.sin(lat_rad) * math.cos(angular)
        + math.cos(lat_rad) * math.sin(angular) * math.cos(bearing_rad)
    )
    new_lon = lon_rad + math.atan2(
        math.sin(bearing_rad) * math.sin(angular) * math.cos(lat_rad),
        math.cos(angular) - math.sin(lat_rad) * math.sin(new_lat),
    )
    return math.degrees(new_lat), math.degrees(new_lon)


def build_ring(
    sensor_lat: float,
    sensor_lon: float,
    *,
    distance_m: float,
    intensity: float,
    samples: int = 12,
) -> list[HeatmapPoint]:
    if samples < 1:
        raise ValueError("samples must be >= 1")
    points: list[HeatmapPoint] = []
    for i in range(samples):
        bearing = (360.0 / samples) * i
        lat, lon = offset_latlon(sensor_lat, sensor_lon, distance_m, bearing)
        points.append(HeatmapPoint(lat=lat, lon=lon, normalized_intensity=intensity))
    return points


class HeatmapEngine:
    """Persists heatmap evidence to SQLite and exposes ATAK-shaped queries."""

    def __init__(self, storage: Storage) -> None:
        self._storage = storage

    def record(
        self,
        *,
        sensor_lat: float,
        sensor_lon: float,
        frequency_hz: float,
        rssi_dbm: float,
        signal_type: str,
        tx_power_dbm: float = 20.0,
        ring_samples: int = 12,
        captured_at: float | None = None,
    ) -> list[HeatmapPoint]:
        captured_at = captured_at if captured_at is not None else time.time()
        distance = free_space_distance_m(rssi_dbm, frequency_hz, tx_power_dbm=tx_power_dbm)
        intensity = normalize_intensity(distance)
        ring = build_ring(
            sensor_lat,
            sensor_lon,
            distance_m=distance,
            intensity=intensity,
            samples=ring_samples,
        )
        records = [
            HeatmapRecord(
                captured_at=captured_at,
                lat=p.lat,
                lon=p.lon,
                frequency_hz=frequency_hz,
                rssi_dbm=rssi_dbm,
                signal_type=signal_type,
                intensity=p.normalized_intensity,
            )
            for p in ring
        ]
        self._storage.add_heatmap_points(records)
        return ring

    def query(
        self,
        *,
        signal_type: str | None = None,
        frequency_hz: float | None = None,
        tolerance_hz: float = 5_000_000.0,
        limit: int = 5000,
    ) -> list[dict]:
        rows = self._storage.query_heatmap(
            signal_type=signal_type,
            frequency_hz=frequency_hz,
            tolerance_hz=tolerance_hz,
            limit=limit,
        )
        return [
            {
                "lat": row["lat"],
                "lon": row["lon"],
                "intensity": row["intensity"],
                "frequency_hz": row["frequency_hz"],
                "rssi_dbm": row["rssi_dbm"],
                "signal_type": row["signal_type"],
                "captured_at": row["captured_at"],
            }
            for row in rows
        ]
