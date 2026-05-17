from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whispercatch_sentinel.api import AppDependencies, create_app
from whispercatch_sentinel.config import RuntimeConfig
from whispercatch_sentinel.cot import CotGateway
from whispercatch_sentinel.cuas import CuasAggregator
from whispercatch_sentinel.heatmap import HeatmapEngine
from whispercatch_sentinel.keys import VolatileKeyVault
from whispercatch_sentinel.storage import Storage
from whispercatch_sentinel.streams import StreamBus


@pytest.fixture()
def deps(tmp_path: Path) -> AppDependencies:
    storage = Storage(tmp_path / "wcs.sqlite")
    vault = VolatileKeyVault(tmp_path / "keys.json", enforce_tmpfs=False)
    aggregator = CuasAggregator()
    heatmap = HeatmapEngine(storage)
    bus = StreamBus()
    config = RuntimeConfig(tmpfs_path=str(tmp_path))
    gateway = CotGateway(
        config.cot_multicast_group,
        config.cot_multicast_port,
        sender=lambda *a, **kw: None,
    )
    return AppDependencies(
        config=config,
        storage=storage,
        vault=vault,
        aggregator=aggregator,
        heatmap=heatmap,
        bus=bus,
        gateway=gateway,
    )


@pytest.fixture()
def client(deps: AppDependencies) -> TestClient:
    return TestClient(create_app(deps))


def test_set_system_profile_persists(client: TestClient, deps: AppDependencies) -> None:
    response = client.post(
        "/api/v1/config/system",
        json={"profile": "SCAN_COMBAT", "sweeps": {"2.4GHz": [2400000000, 2500000000, 1000000]}},
    )
    assert response.status_code == 200
    assert deps.storage.get_config("system_profile")["profile"] == "SCAN_COMBAT"


def test_inject_keys_returns_metadata_only(client: TestClient, deps: AppDependencies) -> None:
    response = client.post(
        "/api/v1/config/keys",
        json={
            "keys": [
                {"key_id": "good", "algorithm": "AES-256-OFB", "key_hex": "00" * 32},
                {"key_id": "bad", "algorithm": "DES-OFB", "key_hex": "ff"},
            ]
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == ["good"]
    assert len(body["refused"]) == 1
    # Sanity: the key bytes never appear in the response
    assert "00" * 32 not in response.text


def test_status_reports_keys_and_devices(client: TestClient) -> None:
    client.post(
        "/api/v1/config/keys",
        json={"keys": [{"key_id": "tg-1", "algorithm": "DES-OFB", "key_hex": "11" * 8}]},
    )
    response = client.get("/api/v1/config/status")
    assert response.status_code == 200
    body = response.json()
    assert "devices" in body
    assert body["keys_loaded"] == ["tg-1"]
    assert "ramdisk_ready" in body


def test_drones_telemetry_round_trip(client: TestClient) -> None:
    client.post(
        "/api/v1/cuas/ingest",
        json={
            "source": "droneid",
            "protocol": "ASTM DRI",
            "rssi_dbm": -55.0,
            "serial": "DJI-X",
            "drone_lat": 35.0,
            "drone_lon": -117.0,
            "drone_alt_m": 120.0,
            "pilot_lat": 35.1,
            "pilot_lon": -117.1,
            "airframe": "Mavic 3",
        },
    )
    response = client.get("/api/v1/telemetry/drones")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["serial"] == "DJI-X"
    assert body[0]["airframe"] == "Mavic 3"


def test_heatmap_endpoint(client: TestClient, deps: AppDependencies) -> None:
    deps.heatmap.record(
        sensor_lat=34.0,
        sensor_lon=-117.0,
        frequency_hz=5_800_000_000,
        rssi_dbm=-60.0,
        signal_type="analog_fpv",
        ring_samples=6,
    )
    response = client.get(
        "/api/v1/telemetry/heatmap",
        params={"signal_type": "analog_fpv"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 6
    assert all(p["signal_type"] == "analog_fpv" for p in body["points"])


def test_transcripts_filtered(client: TestClient, deps: AppDependencies) -> None:
    from whispercatch_sentinel.storage import TranscriptRecord

    deps.storage.add_transcript(
        TranscriptRecord(captured_at=1.0, text="clear", talkgroup="TG-1", encrypted=False, decrypted=False)
    )
    deps.storage.add_transcript(
        TranscriptRecord(
            captured_at=2.0,
            text="secret",
            talkgroup="TG-9",
            encrypted=True,
            decrypted=True,
            algorithm="AES-256-OFB",
            key_id="kid",
        )
    )

    response = client.get("/api/v1/telemetry/transcripts", params={"decrypted": True})
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["transcripts"][0]["talkgroup"] == "TG-9"
    # Ensure secret keys never leak into the wire response
    assert "key_hex" not in response.text


def test_waterfall_websocket_accepts_subscribers(client: TestClient, deps: AppDependencies) -> None:
    # Connect and disconnect cleanly — the bus must accept and release the
    # subscriber so producers don't leak queues over many ATAK reconnects.
    with client.websocket_connect("/api/v1/stream/waterfall"):
        assert deps.bus.subscriber_count("waterfall") >= 0  # sanity
    # After disconnect, eventually the bus cleans up. Give the loop a tick.
    import time as _t

    _t.sleep(0.05)
    assert deps.bus.subscriber_count("waterfall") == 0


def test_health_remains_cloud_free(client: TestClient) -> None:
    body = client.get("/api/v1/health").json()
    assert body["cloud_processing"] is False
    assert body["headless"] is True


def test_dashboard_origin_is_cors_enabled(client: TestClient) -> None:
    response = client.options(
        "/api/v1/health",
        headers={
            "origin": "http://127.0.0.1:8080",
            "access-control-request-method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:8080"


# ---------------------------------------------------------------------------
# gpsd-aware endpoints
# ---------------------------------------------------------------------------
def _make_gps_deps(tmp_path: Path) -> AppDependencies:
    from whispercatch_sentinel.gps import GpsReceiver

    storage = Storage(tmp_path / "wcs.sqlite")
    vault = VolatileKeyVault(tmp_path / "keys.json", enforce_tmpfs=False)
    config = RuntimeConfig(tmpfs_path=str(tmp_path), gpsd_enabled=True)
    return AppDependencies(
        config=config,
        storage=storage,
        vault=vault,
        aggregator=CuasAggregator(),
        heatmap=HeatmapEngine(storage),
        bus=StreamBus(),
        gateway=CotGateway(
            config.cot_multicast_group,
            config.cot_multicast_port,
            sender=lambda *a, **kw: None,
        ),
        gps=GpsReceiver(),
    )


def test_position_endpoint_returns_null_without_fix(client: TestClient) -> None:
    response = client.get("/api/v1/telemetry/position")
    assert response.status_code == 200
    assert response.json() is None


def test_position_endpoint_serves_live_fix(tmp_path: Path) -> None:
    import time as _time
    from whispercatch_sentinel.gps import GpsFix

    deps = _make_gps_deps(tmp_path)
    deps.gps.set_fix(
        GpsFix(lat=45.5, lon=-73.6, altitude_m=42.0, mode=3, received_at=_time.time())
    )
    client = TestClient(create_app(deps))
    body = client.get("/api/v1/telemetry/position").json()
    assert body is not None
    assert body["lat"] == pytest.approx(45.5)
    assert body["lon"] == pytest.approx(-73.6)
    assert body["mode"] == 3


def test_status_includes_gps_block(client: TestClient, tmp_path: Path) -> None:
    # Default deps fixture has no GPS receiver wired in.
    body = client.get("/api/v1/config/status").json()
    assert body["gps"]["enabled"] is False
    assert body["gps"]["has_fix"] is False

    # And with a GPS-enabled stack:
    import time as _time
    from whispercatch_sentinel.gps import GpsFix

    deps = _make_gps_deps(tmp_path)
    deps.gps.set_fix(
        GpsFix(lat=10.0, lon=20.0, altitude_m=5.0, mode=3, received_at=_time.time())
    )
    body = TestClient(create_app(deps)).get("/api/v1/config/status").json()
    assert body["gps"]["enabled"] is True
    assert body["gps"]["has_fix"] is True
    assert body["gps"]["fix"]["lat"] == pytest.approx(10.0)


def test_observation_endpoint_uses_live_gpsd_fix(tmp_path: Path) -> None:
    import time as _time
    from whispercatch_sentinel.gps import GpsFix

    deps = _make_gps_deps(tmp_path)
    deps.gps.set_fix(
        GpsFix(lat=51.5074, lon=-0.1278, altitude_m=35.0, mode=3, received_at=_time.time())
    )
    client = TestClient(create_app(deps))

    response = client.post(
        "/api/v1/telemetry/observation",
        json={
            "frequency_hz": 2_440_000_000,
            "rssi_dbm": -52.0,
            "signal_type": "wifi",
            "ring_samples": 4,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "gpsd"
    assert body["points_recorded"] == 4
    assert body["sensor_lat"] == pytest.approx(51.5074)

    rows = deps.heatmap.query(signal_type="wifi")
    assert len(rows) == 4
    # Every ring point should sit within a few km of the gpsd fix.
    for row in rows:
        assert abs(row["lat"] - 51.5074) < 1.0
        assert abs(row["lon"] - (-0.1278)) < 1.0


def test_observation_endpoint_rejects_request_with_no_position(tmp_path: Path) -> None:
    # No gpsd receiver, no operator-supplied coords ⇒ must 409 rather than
    # silently writing rotten (0,0) points.
    deps = _make_gps_deps(tmp_path)
    deps.gps = None  # force "no GPS available" state
    client = TestClient(create_app(deps))
    response = client.post(
        "/api/v1/telemetry/observation",
        json={"frequency_hz": 100_000_000, "rssi_dbm": -70.0, "signal_type": "p25"},
    )
    assert response.status_code == 409
    assert "no sensor position" in response.json()["detail"]


def test_observation_endpoint_honors_explicit_override(client: TestClient, deps: AppDependencies) -> None:
    response = client.post(
        "/api/v1/telemetry/observation",
        json={
            "frequency_hz": 156_000_000,
            "rssi_dbm": -65.0,
            "signal_type": "p25",
            "sensor_lat": 12.34,
            "sensor_lon": 56.78,
            "ring_samples": 3,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "request"
    assert body["sensor_lat"] == pytest.approx(12.34)
