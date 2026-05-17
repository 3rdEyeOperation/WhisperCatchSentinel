from __future__ import annotations

import os
from os.path import normpath
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


# SDR role type — three distinct roles that map to the guided operator workflow.
#   scout  : wideband sweep / recon / environmental awareness (typically HackRF One)
#   action : focused follow-up on a selected target, e.g. voice/transcription (RTL-SDR V4)
#   aux    : secondary follow-up channel, e.g. analog FPV / video (HackRF One Aux)
SdrRole = Literal["scout", "action", "aux"]


class DeviceProfile(BaseModel):
    name: str
    purpose: str
    usb_path_hint: str


class SdrDeviceConfig(BaseModel):
    """Configuration descriptor for one SDR radio with an assigned operator role."""

    name: str
    purpose: str
    usb_path_hint: str
    role: SdrRole
    capabilities: list[str] = []


class RuntimeConfig(BaseModel):
    headless: bool = True
    cloud_processing: bool = False
    architecture: str = Field(default_factory=lambda: os.uname().machine)
    tmpfs_path: str = "/dev/shm/whispercatch"
    sdrangel_rest: str = "http://127.0.0.1:8091"
    trunk_recorder_host: str = "127.0.0.1"
    droneid_daemon: str = "droneid-go"
    whisper_cpp_binary: str = "whisper-cli"
    cot_multicast_group: str = "239.2.3.1"
    cot_multicast_port: int = 6969
    # gpsd integration — the sensor box reads its live location from gpsd so
    # every heatmap point is stamped with the actual antenna position
    # instead of an operator-typed coordinate. Disabled by default so unit
    # tests and laptops without GPS don't try to open a TCP socket.
    gpsd_enabled: bool = False
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947


HARDWARE_PROFILES = [
    DeviceProfile(
        name="HackRF One",
        purpose="DJI DroneID / 5.8GHz Analog FPV",
        usb_path_hint="/dev/bus/usb",
    ),
    DeviceProfile(
        name="RTL-SDR V4",
        purpose="VHF/UHF P25/DMR voice",
        usb_path_hint="/dev/bus/usb",
    ),
    DeviceProfile(
        name="HackRF One (Aux)",
        purpose="Analog FPV / secondary wideband",
        usb_path_hint="/dev/bus/usb",
    ),
    DeviceProfile(
        name="Alfa USB Wi-Fi",
        purpose="802.11 monitor mode",
        usb_path_hint="/sys/class/net",
    ),
    DeviceProfile(
        name="Sniffle BLE Coded PHY",
        purpose="BLE telemetry and Coded PHY sniffing",
        usb_path_hint="/dev/serial/by-id",
    ),
    DeviceProfile(
        name="gpsd GPS receiver",
        purpose="Live sensor position for heatmap stamping (USB/UART GPS via gpsd)",
        usb_path_hint="/dev/gpsd",
    ),
]

# Three-SDR operator setup.  Each radio has a default role that maps to the
# guided workflow.  Operators can override role assignments at runtime via
# the PATCH /api/v1/sdr/assign endpoint; the defaults below are what the
# system boots with on a fresh deploy.
SDR_DEVICES: list[SdrDeviceConfig] = [
    SdrDeviceConfig(
        name="HackRF One",
        purpose="Wideband scout: sweep/recon, DJI DroneID, analog FPV (HF–6 GHz)",
        usb_path_hint="/dev/bus/usb",
        role="scout",
        capabilities=["wideband_sweep", "analog_fpv", "droneid"],
    ),
    SdrDeviceConfig(
        name="RTL-SDR V4",
        purpose="Action radio: VHF/UHF voice monitoring and AI transcription (P25/DMR)",
        usb_path_hint="/dev/bus/usb",
        role="action",
        capabilities=["vhf_voice", "uhf_voice", "p25", "dmr", "transcription"],
    ),
    SdrDeviceConfig(
        name="HackRF One (Aux)",
        purpose="Aux/video radio: analog FPV decode, secondary wideband channel",
        usb_path_hint="/dev/bus/usb",
        role="aux",
        capabilities=["analog_fpv", "wideband_sweep", "atv"],
    ),
]


@dataclass(frozen=True)
class DeviceStatus:
    name: str
    connected: bool
    detail: str


def detect_device(profile: DeviceProfile) -> DeviceStatus:
    """Best-effort USB state detection with transient-safe error handling."""
    try:
        exists = Path(profile.usb_path_hint).exists()
    except OSError as exc:
        return DeviceStatus(
            name=profile.name,
            connected=False,
            detail=f"transient_usb_error:{type(exc).__name__}",
        )

    return DeviceStatus(
        name=profile.name,
        connected=exists,
        detail="ready" if exists else "missing",
    )


def is_tmpfs_ramdisk(path: str) -> bool:
    """Validate that crypto workspace is volatile RAM-backed storage."""
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as mounts:
            return any(
                len(parts := line.split()) >= 3
                and normpath(parts[1]) == normpath(path)
                and parts[2] == "tmpfs"
                for line in mounts
            )
    except OSError:
        return False
