"""Tests for SDR device management endpoints (3-SDR operator setup with
hardware-capability-aware role validation, plus BladeRF / AntSDR alternates).

Covers:
- GET /api/v1/sdr/devices — list devices with default roles & capabilities
- PATCH /api/v1/sdr/assign — reassign a role at runtime, gated by supported_roles
- GET /api/v1/config/status — includes sdr_devices with bandwidth/supported_roles
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


# Names of the three radios that ship assigned to default operator roles.
DEFAULT_THREE = {"HackRF One", "RTL-SDR V4", "HackRF One (Aux)"}
# Alternate wideband radios registered but role-swappable by the operator.
ALTERNATE_WIDEBAND = {"BladeRF 2.0 micro", "AntSDR E200"}


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

def test_sdr_devices_includes_three_default_radios() -> None:
    names = {sdr.name for sdr in SDR_DEVICES}
    assert DEFAULT_THREE.issubset(names)


def test_sdr_devices_default_roles_include_all_three() -> None:
    # The default-active roles (across the curated three radios) must cover
    # the three operator workflows.
    default_roles = {
        sdr.role for sdr in SDR_DEVICES if sdr.name in DEFAULT_THREE
    }
    # action is required (RTL-SDR); scout is required (HackRF).
    assert "scout" in default_roles
    assert "action" in default_roles


def test_sdr_devices_each_have_capabilities_and_bandwidth() -> None:
    for sdr in SDR_DEVICES:
        assert len(sdr.capabilities) >= 1, f"{sdr.name} has no capabilities"
        assert sdr.bandwidth_hz > 0, f"{sdr.name} missing bandwidth_hz"
        assert len(sdr.supported_roles) >= 1, f"{sdr.name} has no supported_roles"


def test_rtl_sdr_only_supports_action_role() -> None:
    """RTL-SDR has ~2.4 MHz BW — too narrow to sweep or decode 6 MHz FPV."""
    rtl = next(s for s in SDR_DEVICES if s.name == "RTL-SDR V4")
    assert rtl.supported_roles == ["action"]
    # And its sticker bandwidth should reflect that limit.
    assert rtl.bandwidth_hz < 5_000_000


def test_hackrf_supports_scout_and_aux_but_not_action_default() -> None:
    """HackRF is wideband (~20 MHz) — scout and aux roles only by default."""
    hackrf = next(s for s in SDR_DEVICES if s.name == "HackRF One")
    assert "scout" in hackrf.supported_roles
    assert "aux" in hackrf.supported_roles
    assert hackrf.bandwidth_hz >= 10_000_000


def test_aux_hackrf_defaults_to_scout_not_aux() -> None:
    """A wideband second SDR is wasted on follow-up duty; default it to scout."""
    aux = next(s for s in SDR_DEVICES if s.name == "HackRF One (Aux)")
    assert aux.role == "scout"
    assert "scout" in aux.supported_roles
    assert "aux" in aux.supported_roles


def test_bladerf_and_antsdr_are_registered_as_wideband_alternates() -> None:
    names = {sdr.name for sdr in SDR_DEVICES}
    assert ALTERNATE_WIDEBAND.issubset(names)
    for alt_name in ALTERNATE_WIDEBAND:
        alt = next(s for s in SDR_DEVICES if s.name == alt_name)
        # Wideband ⇒ all three roles supported.
        assert set(alt.supported_roles) == {"scout", "action", "aux"}
        # And the bandwidth must reflect their wideband front-ends.
        assert alt.bandwidth_hz >= 40_000_000


# ---------------------------------------------------------------------------
# GET /api/v1/sdr/devices
# ---------------------------------------------------------------------------

def test_list_sdr_devices_returns_all_registered_radios(client: TestClient) -> None:
    response = client.get("/api/v1/sdr/devices")
    assert response.status_code == 200
    devices = response.json()
    names = {d["name"] for d in devices}
    assert DEFAULT_THREE.issubset(names)
    assert ALTERNATE_WIDEBAND.issubset(names)


def test_list_sdr_devices_default_roles_cover_workflow(client: TestClient) -> None:
    response = client.get("/api/v1/sdr/devices")
    assert response.status_code == 200
    devices = {d["name"]: d for d in response.json()}
    assert devices["HackRF One"]["role"] == "scout"
    assert devices["RTL-SDR V4"]["role"] == "action"
    assert devices["HackRF One (Aux)"]["role"] == "scout"


def test_list_sdr_devices_includes_capability_metadata(client: TestClient) -> None:
    response = client.get("/api/v1/sdr/devices")
    assert response.status_code == 200
    for device in response.json():
        for field in (
            "name", "role", "purpose", "capabilities",
            "bandwidth_hz", "supported_roles", "connected", "detail",
        ):
            assert field in device, f"missing {field!r} in payload"
        assert isinstance(device["bandwidth_hz"], int)
        assert isinstance(device["supported_roles"], list)


# ---------------------------------------------------------------------------
# PATCH /api/v1/sdr/assign  (hardware-capability-gated)
# ---------------------------------------------------------------------------

def test_assign_valid_role_persists_and_reflected_in_list(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/sdr/assign",
        json={"device_name": "HackRF One", "role": "aux"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "device_name": "HackRF One", "role": "aux"}
    devices = {d["name"]: d for d in client.get("/api/v1/sdr/devices").json()}
    assert devices["HackRF One"]["role"] == "aux"


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


def test_assign_rejects_scout_on_rtl_sdr(client: TestClient) -> None:
    """RTL-SDR cannot sweep wideband — assignment must be refused."""
    response = client.patch(
        "/api/v1/sdr/assign",
        json={"device_name": "RTL-SDR V4", "role": "scout"},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "RTL-SDR V4" in detail
    assert "scout" in detail


def test_assign_rejects_aux_on_rtl_sdr(client: TestClient) -> None:
    """RTL-SDR cannot decode 6 MHz analog FPV — refuse aux assignment."""
    response = client.patch(
        "/api/v1/sdr/assign",
        json={"device_name": "RTL-SDR V4", "role": "aux"},
    )
    assert response.status_code == 422


def test_assign_accepts_action_on_bladerf(client: TestClient) -> None:
    """Wideband BladeRF can serve any of the three roles."""
    response = client.patch(
        "/api/v1/sdr/assign",
        json={"device_name": "BladeRF 2.0 micro", "role": "action"},
    )
    assert response.status_code == 200


def test_assign_accepts_aux_on_antsdr(client: TestClient) -> None:
    response = client.patch(
        "/api/v1/sdr/assign",
        json={"device_name": "AntSDR E200", "role": "aux"},
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/v1/config/status  (sdr_devices block)
# ---------------------------------------------------------------------------

def test_status_includes_sdr_devices_block_with_capabilities(client: TestClient) -> None:
    body = client.get("/api/v1/config/status").json()
    assert "sdr_devices" in body
    assert len(body["sdr_devices"]) == len(SDR_DEVICES)
    for device in body["sdr_devices"]:
        assert "bandwidth_hz" in device
        assert "supported_roles" in device


def test_status_sdr_devices_roles_reflect_override(client: TestClient) -> None:
    client.patch(
        "/api/v1/sdr/assign",
        json={"device_name": "HackRF One (Aux)", "role": "aux"},
    )
    body = client.get("/api/v1/config/status").json()
    aux = next(d for d in body["sdr_devices"] if d["name"] == "HackRF One (Aux)")
    assert aux["role"] == "aux"

