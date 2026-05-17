"""Tests for three-SDR device management endpoints.

Covers:
- GET /api/v1/sdr/devices — list devices with default roles
- PATCH /api/v1/sdr/assign — reassign a role at runtime
- GET /api/v1/config/status — includes sdr_devices with role info
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from whispercatch_sentinel.api import AppDependencies, create_app
from whispercatch_sentinel.config import SDR_DEVICES, RuntimeConfig
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
        aggregator=CuasAggregator(),
        heatmap=HeatmapEngine(storage),
        bus=StreamBus(),
        gateway=gateway,
    )


@pytest.fixture()
def client(deps: AppDependencies) -> TestClient:
    return TestClient(create_app(deps))


# ---------------------------------------------------------------------------
# Config module unit tests
# ---------------------------------------------------------------------------

def test_sdr_devices_constant_has_exactly_three_entries() -> None:
    assert len(SDR_DEVICES) == 3


def test_sdr_devices_have_required_roles() -> None:
    roles = {sdr.role for sdr in SDR_DEVICES}
    assert roles == {"scout", "action", "aux"}


def test_sdr_devices_each_have_capabilities() -> None:
    for sdr in SDR_DEVICES:
        assert len(sdr.capabilities) >= 1, f"{sdr.name} has no capabilities"


# ---------------------------------------------------------------------------
# GET /api/v1/sdr/devices
# ---------------------------------------------------------------------------

def test_list_sdr_devices_returns_three_radios(client: TestClient) -> None:
    response = client.get("/api/v1/sdr/devices")
    assert response.status_code == 200
    devices = response.json()
    assert len(devices) == 3


def test_list_sdr_devices_default_roles(client: TestClient) -> None:
    response = client.get("/api/v1/sdr/devices")
    assert response.status_code == 200
    devices = response.json()
    roles = {d["role"] for d in devices}
    assert roles == {"scout", "action", "aux"}


def test_list_sdr_devices_includes_required_fields(client: TestClient) -> None:
    response = client.get("/api/v1/sdr/devices")
    assert response.status_code == 200
    for device in response.json():
        assert "name" in device
        assert "role" in device
        assert "purpose" in device
        assert "capabilities" in device
        assert "connected" in device
        assert "detail" in device


def test_list_sdr_devices_known_device_names(client: TestClient) -> None:
    response = client.get("/api/v1/sdr/devices")
    assert response.status_code == 200
    names = {d["name"] for d in response.json()}
    assert "HackRF One" in names
    assert "RTL-SDR V4" in names
    assert "HackRF One (Aux)" in names


# ---------------------------------------------------------------------------
# PATCH /api/v1/sdr/assign
# ---------------------------------------------------------------------------

def test_assign_role_persists_and_reflected_in_list(
    client: TestClient,
) -> None:
    # Reassign HackRF One from scout → action
    response = client.patch(
        "/api/v1/sdr/assign",
        json={"device_name": "HackRF One", "role": "action"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["device_name"] == "HackRF One"
    assert body["role"] == "action"

    # Verify the list endpoint now reflects the override.
    devices = client.get("/api/v1/sdr/devices").json()
    hackrf = next(d for d in devices if d["name"] == "HackRF One")
    assert hackrf["role"] == "action"


def test_assign_role_unknown_device_returns_404(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/sdr/assign",
        json={"device_name": "NonExistent SDR", "role": "scout"},
    )
    assert response.status_code == 404
    assert "NonExistent SDR" in response.json()["detail"]


def test_assign_role_invalid_role_returns_422(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/sdr/assign",
        json={"device_name": "HackRF One", "role": "not_a_role"},
    )
    assert response.status_code == 422


def test_reassign_all_three_roles(client: TestClient) -> None:
    """Rotate roles: RTL-SDR → scout, HackRF One → action, Aux → aux."""
    assignments = [
        ("RTL-SDR V4", "scout"),
        ("HackRF One", "action"),
        ("HackRF One (Aux)", "aux"),
    ]
    for device_name, role in assignments:
        resp = client.patch(
            "/api/v1/sdr/assign",
            json={"device_name": device_name, "role": role},
        )
        assert resp.status_code == 200

    devices = client.get("/api/v1/sdr/devices").json()
    by_name = {d["name"]: d for d in devices}
    assert by_name["RTL-SDR V4"]["role"] == "scout"
    assert by_name["HackRF One"]["role"] == "action"
    assert by_name["HackRF One (Aux)"]["role"] == "aux"


# ---------------------------------------------------------------------------
# GET /api/v1/config/status  (sdr_devices block)
# ---------------------------------------------------------------------------

def test_status_includes_sdr_devices_block(client: TestClient) -> None:
    response = client.get("/api/v1/config/status")
    assert response.status_code == 200
    body = response.json()
    assert "sdr_devices" in body
    assert len(body["sdr_devices"]) == 3


def test_status_sdr_devices_roles_reflect_override(
    client: TestClient,
) -> None:
    # Override RTL-SDR V4 to aux role.
    client.patch(
        "/api/v1/sdr/assign",
        json={"device_name": "RTL-SDR V4", "role": "aux"},
    )
    body = client.get("/api/v1/config/status").json()
    rtl = next(d for d in body["sdr_devices"] if d["name"] == "RTL-SDR V4")
    assert rtl["role"] == "aux"


def test_status_sdr_devices_default_includes_all_roles(
    client: TestClient,
) -> None:
    body = client.get("/api/v1/config/status").json()
    roles = {d["role"] for d in body["sdr_devices"]}
    assert roles == {"scout", "action", "aux"}
