"""System status probes for ``GET /api/v1/config/status``."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .config import HARDWARE_PROFILES, SDR_DEVICES, DeviceProfile, SdrDeviceConfig, DeviceStatus, detect_device


@dataclass(frozen=True)
class SystemStatus:
    cpu_temp_c: float | None
    npu_load_pct: float | None
    cpu_load_pct: float | None
    devices: list[DeviceStatus]
    ramdisk_ready: bool


def _read_first_float(path: str, divisor: float = 1.0) -> float | None:
    try:
        return float(Path(path).read_text().strip()) / divisor
    except (OSError, ValueError):
        return None


def cpu_temperature_c() -> float | None:
    """Best-effort CPU temperature probe for Raspberry Pi 5 / generic Linux."""
    for candidate, divisor in (
        ("/sys/class/thermal/thermal_zone0/temp", 1000.0),
        ("/sys/devices/virtual/thermal/thermal_zone0/temp", 1000.0),
    ):
        value = _read_first_float(candidate, divisor)
        if value is not None:
            return value
    return None


def cpu_load_pct() -> float | None:
    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as fh:
            load_1m = float(fh.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    try:
        cpu_count = max(1, len([p for p in Path("/sys/devices/system/cpu").glob("cpu[0-9]*")]))
    except OSError:
        cpu_count = 1
    return min(100.0, (load_1m / cpu_count) * 100.0)


def npu_load_pct() -> float | None:
    """Hailo-8 NPU load probe.

    The Hailo runtime exposes utilisation via ``/sys/class/hailo*/load`` on
    supported kernel drivers. Returns ``None`` when the accelerator isn't
    present so the API can advertise "not connected" cleanly.
    """
    for path in Path("/sys/class").glob("hailo*/load"):
        value = _read_first_float(str(path))
        if value is not None:
            return min(100.0, max(0.0, value))
    return None


def build_sdr_device_entry(sdr: SdrDeviceConfig, effective_role: str) -> dict:
    """Probe one SDR device's connection state and return its status dict."""
    profile = DeviceProfile(
        name=sdr.name,
        purpose=sdr.purpose,
        usb_path_hint=sdr.usb_path_hint,
    )
    ds = detect_device(profile)
    return {
        "name": sdr.name,
        "role": effective_role,
        "purpose": sdr.purpose,
        "capabilities": sdr.capabilities,
        "bandwidth_hz": sdr.bandwidth_hz,
        "supported_roles": sdr.supported_roles,
        "connected": ds.connected,
        "detail": ds.detail,
    }


def collect_status(ramdisk_ready: bool, sdr_role_overrides: dict | None = None) -> dict:
    status = SystemStatus(
        cpu_temp_c=cpu_temperature_c(),
        npu_load_pct=npu_load_pct(),
        cpu_load_pct=cpu_load_pct(),
        devices=[detect_device(profile) for profile in HARDWARE_PROFILES],
        ramdisk_ready=ramdisk_ready,
    )
    # Build the three-SDR device list, applying any runtime role overrides.
    overrides = sdr_role_overrides or {}
    sdr_devices = [
        build_sdr_device_entry(sdr, overrides.get(sdr.name, sdr.role))
        for sdr in SDR_DEVICES
    ]
    return {
        "cpu_temp_c": status.cpu_temp_c,
        "npu_load_pct": status.npu_load_pct,
        "cpu_load_pct": status.cpu_load_pct,
        "ramdisk_ready": status.ramdisk_ready,
        "devices": [asdict(d) for d in status.devices],
        "sdr_devices": sdr_devices,
    }
