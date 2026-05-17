"""Tests for the gpsd integration.

These tests stand up a tiny localhost TCP server that mimics the gpsd JSON
wire protocol (line-delimited frames). No real GPS hardware or daemon is
required, and the suite remains fully offline.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from whispercatch_sentinel.gps import (
    GpsdClient,
    GpsFix,
    GpsReceiver,
    parse_tpv,
)


# ---------------------------------------------------------------------------
# Pure parser tests
# ---------------------------------------------------------------------------
def test_parse_tpv_extracts_3d_fix() -> None:
    fix = parse_tpv(
        {
            "class": "TPV",
            "mode": 3,
            "time": "2024-05-17T11:22:33.000Z",
            "lat": 34.0522,
            "lon": -118.2437,
            "altHAE": 91.5,
            "speed": 1.4,
            "track": 270.0,
            "eph": 2.5,
            "device": "/dev/ttyUSB0",
        }
    )
    assert fix is not None
    assert fix.lat == pytest.approx(34.0522)
    assert fix.lon == pytest.approx(-118.2437)
    assert fix.altitude_m == pytest.approx(91.5)
    assert fix.has_3d
    assert fix.device == "/dev/ttyUSB0"
    assert fix.eph_m == pytest.approx(2.5)


def test_parse_tpv_ignores_no_fix_and_other_classes() -> None:
    assert parse_tpv({"class": "TPV", "mode": 1, "lat": 0.0, "lon": 0.0}) is None
    assert parse_tpv({"class": "SKY", "satellites": []}) is None
    assert parse_tpv({"class": "TPV", "mode": 3}) is None  # missing lat/lon
    assert parse_tpv({"class": "VERSION", "release": "3.20"}) is None


def test_parse_tpv_falls_back_to_alt_msl_then_legacy_alt() -> None:
    fix = parse_tpv({"class": "TPV", "mode": 2, "lat": 1.0, "lon": 2.0, "altMSL": 12.0})
    assert fix is not None and fix.altitude_m == pytest.approx(12.0)
    fix = parse_tpv({"class": "TPV", "mode": 2, "lat": 1.0, "lon": 2.0, "alt": 9.0})
    assert fix is not None and fix.altitude_m == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# Fake gpsd server fixture
# ---------------------------------------------------------------------------
class _FakeGpsd:
    """Minimal localhost server that scripts a sequence of gpsd JSON frames."""

    def __init__(self, frames: list[bytes]) -> None:
        self.frames = frames
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self.client_watch_payload: bytes | None = None

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(2.0)
            # Send VERSION first (real gpsd does).
            conn.sendall(b'{"class":"VERSION","release":"3.20","rev":"3.20"}\n')
            try:
                self.client_watch_payload = conn.recv(256)
            except OSError:
                self.client_watch_payload = b""
            for frame in self.frames:
                try:
                    conn.sendall(frame + b"\n")
                except OSError:
                    return
                time.sleep(0.01)
            # Hold connection briefly so the client can drain the buffer
            # before we close it.
            time.sleep(0.1)

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture()
def fake_gpsd():
    servers: list[_FakeGpsd] = []

    def factory(frames: list[bytes]) -> _FakeGpsd:
        server = _FakeGpsd(frames)
        server.start()
        servers.append(server)
        return server

    yield factory
    for srv in servers:
        srv.close()


# ---------------------------------------------------------------------------
# Client + receiver integration tests
# ---------------------------------------------------------------------------
def test_gpsdclient_returns_first_usable_fix(fake_gpsd) -> None:
    server = fake_gpsd(
        [
            b'{"class":"DEVICES","devices":[]}',
            b'{"class":"TPV","mode":1}',  # no-fix frame is skipped
            b'{"class":"SKY","satellites":[]}',
            b'{"class":"TPV","mode":3,"lat":47.6062,"lon":-122.3321,"altHAE":56.0}',
            b'{"class":"TPV","mode":3,"lat":99.0,"lon":99.0}',  # never reached
        ]
    )
    client = GpsdClient(host="127.0.0.1", port=server.port, read_timeout=1.5)
    fix = client.read_fix()
    assert fix is not None
    assert fix.lat == pytest.approx(47.6062)
    assert fix.lon == pytest.approx(-122.3321)
    # Confirm the client actually sent a WATCH enable JSON request.
    assert server.client_watch_payload is not None
    assert b'?WATCH=' in server.client_watch_payload
    assert b'"enable":true' in server.client_watch_payload


def test_gpsdclient_returns_none_when_no_fix_in_stream(fake_gpsd) -> None:
    server = fake_gpsd(
        [
            b'{"class":"TPV","mode":1}',
            b'{"class":"SKY","satellites":[]}',
        ]
    )
    client = GpsdClient(host="127.0.0.1", port=server.port, read_timeout=1.0)
    assert client.read_fix(max_frames=5) is None


def test_gpsdclient_handles_unreachable_daemon() -> None:
    # Bind + close to grab an almost-certainly-free port number.
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()
    client = GpsdClient(host="127.0.0.1", port=closed_port, connect_timeout=0.3, read_timeout=0.3)
    assert client.read_fix() is None


# ---------------------------------------------------------------------------
# GpsReceiver state tests (no real daemon needed)
# ---------------------------------------------------------------------------
def test_receiver_returns_none_when_no_fix_set() -> None:
    receiver = GpsReceiver()
    assert receiver.latest_fix() is None
    assert receiver.running is False


def test_receiver_set_fix_round_trip() -> None:
    receiver = GpsReceiver()
    fix = GpsFix(
        lat=10.0, lon=20.0, altitude_m=5.0, mode=3, received_at=time.time()
    )
    receiver.set_fix(fix)
    assert receiver.latest_fix() == fix


def test_receiver_drops_stale_fix() -> None:
    receiver = GpsReceiver(fix_ttl_s=0.05)
    fix = GpsFix(
        lat=10.0, lon=20.0, altitude_m=5.0, mode=3, received_at=time.time() - 1.0
    )
    receiver.set_fix(fix)
    assert receiver.latest_fix() is None


# ---------------------------------------------------------------------------
# API lifecycle: the backend should own the gpsd receiver thread
# ---------------------------------------------------------------------------
def test_app_startup_starts_and_shutdown_stops_gps_receiver(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from whispercatch_sentinel.api import AppDependencies, create_app
    from whispercatch_sentinel.config import RuntimeConfig
    from whispercatch_sentinel.cot import CotGateway
    from whispercatch_sentinel.cuas import CuasAggregator
    from whispercatch_sentinel.heatmap import HeatmapEngine
    from whispercatch_sentinel.keys import VolatileKeyVault
    from whispercatch_sentinel.storage import Storage
    from whispercatch_sentinel.streams import StreamBus

    started: list[bool] = []
    stopped: list[bool] = []

    class _SpyReceiver(GpsReceiver):
        def start(self) -> None:  # type: ignore[override]
            started.append(True)

        def stop(self, timeout: float | None = 2.0) -> None:  # type: ignore[override]
            stopped.append(True)

    storage = Storage(tmp_path / "wcs.sqlite")
    vault = VolatileKeyVault(tmp_path / "keys.json", enforce_tmpfs=False)
    config = RuntimeConfig(tmpfs_path=str(tmp_path), gpsd_enabled=True)
    deps = AppDependencies(
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
        gps=_SpyReceiver(),
    )
    # Entering the TestClient context triggers FastAPI startup; exiting
    # triggers shutdown. The spy verifies both halves of the lifecycle.
    with TestClient(create_app(deps)) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert started == [True]
        assert stopped == []
    assert stopped == [True]


def test_app_lifecycle_is_safe_without_gps(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from whispercatch_sentinel.api import AppDependencies, create_app
    from whispercatch_sentinel.config import RuntimeConfig
    from whispercatch_sentinel.cot import CotGateway
    from whispercatch_sentinel.cuas import CuasAggregator
    from whispercatch_sentinel.heatmap import HeatmapEngine
    from whispercatch_sentinel.keys import VolatileKeyVault
    from whispercatch_sentinel.storage import Storage
    from whispercatch_sentinel.streams import StreamBus

    storage = Storage(tmp_path / "wcs.sqlite")
    vault = VolatileKeyVault(tmp_path / "keys.json", enforce_tmpfs=False)
    config = RuntimeConfig(tmpfs_path=str(tmp_path))
    deps = AppDependencies(
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
        gps=None,
    )
    with TestClient(create_app(deps)) as client:
        assert client.get("/api/v1/health").status_code == 200


def test_default_dependencies_honor_gpsd_env_vars(monkeypatch) -> None:
    from whispercatch_sentinel.api.factory import _build_default_dependencies

    monkeypatch.setenv("WHISPERCATCH_GPSD_ENABLED", "1")
    monkeypatch.setenv("WHISPERCATCH_GPSD_HOST", "10.0.0.7")
    monkeypatch.setenv("WHISPERCATCH_GPSD_PORT", "3737")
    deps = _build_default_dependencies()
    assert deps.config.gpsd_enabled is True
    assert deps.config.gpsd_host == "10.0.0.7"
    assert deps.config.gpsd_port == 3737
    assert deps.gps is not None
    assert deps.gps.host == "10.0.0.7"
    assert deps.gps.port == 3737


def test_default_dependencies_skip_gps_when_env_disables(monkeypatch) -> None:
    from whispercatch_sentinel.api.factory import _build_default_dependencies

    monkeypatch.delenv("WHISPERCATCH_GPSD_ENABLED", raising=False)
    deps = _build_default_dependencies()
    assert deps.config.gpsd_enabled is False
    assert deps.gps is None
