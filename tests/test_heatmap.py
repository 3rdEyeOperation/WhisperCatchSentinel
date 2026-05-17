import math

from whispercatch_sentinel.heatmap import (
    HeatmapEngine,
    build_ring,
    free_space_distance_m,
    normalize_intensity,
    offset_latlon,
)
from whispercatch_sentinel.storage import Storage


def test_friis_distance_monotonic_with_rssi() -> None:
    near = free_space_distance_m(rssi_dbm=-40.0, frequency_hz=2_400_000_000)
    far = free_space_distance_m(rssi_dbm=-90.0, frequency_hz=2_400_000_000)
    assert near < far
    assert near >= 1.0


def test_normalize_intensity_bounded() -> None:
    assert normalize_intensity(0.0) == 1.0
    assert normalize_intensity(10**9) == 0.0
    assert 0.0 < normalize_intensity(2500.0) < 1.0


def test_offset_latlon_great_circle() -> None:
    lat, lon = offset_latlon(0.0, 0.0, distance_m=111_320.0, bearing_deg=90.0)
    assert math.isclose(lat, 0.0, abs_tol=1e-6)
    assert math.isclose(lon, 1.0, abs_tol=1e-2)


def test_build_ring_has_uniform_samples() -> None:
    ring = build_ring(0.0, 0.0, distance_m=1000.0, intensity=0.5, samples=8)
    assert len(ring) == 8
    assert all(point.normalized_intensity == 0.5 for point in ring)


def test_engine_persists_and_queries() -> None:
    storage = Storage(":memory:")
    engine = HeatmapEngine(storage)
    points = engine.record(
        sensor_lat=34.0,
        sensor_lon=-117.0,
        frequency_hz=5_800_000_000,
        rssi_dbm=-60.0,
        signal_type="analog_fpv",
        ring_samples=4,
    )
    assert len(points) == 4

    rows = engine.query(signal_type="analog_fpv")
    assert len(rows) == 4
    assert all(row["signal_type"] == "analog_fpv" for row in rows)
    assert all(0.0 <= row["intensity"] <= 1.0 for row in rows)
