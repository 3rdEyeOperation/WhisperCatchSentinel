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
    """Configuration descriptor for one SDR radio with an assigned operator role.

    ``bandwidth_hz`` is the device's *usable instantaneous* RX bandwidth — this
    is what gates which roles the radio can serve:
      * ``scout`` (wideband sweep / recon)  needs a wideband front-end
        (HackRF ~20 MHz, BladeRF ~40-56 MHz, AntSDR ~56 MHz).
      * ``aux``   (analog FPV / ATV decode) needs >= ~8 MHz of contiguous
        spectrum since NTSC/PAL composite video is ~6 MHz wide and FPV pilots
        are commonly spaced 5-10 MHz apart in 5.8 GHz.
      * ``action`` (narrowband voice / P25 / DMR) only needs ~2 MHz and is
        the only role an RTL-SDR can sensibly perform.

    ``supported_roles`` is the explicit allow-list — operators can only
    re-assign a device to a role in this list.  This prevents nonsensical
    setups like asking an RTL-SDR V4 (2.4 MHz BW) to be the wideband scout.
    """

    name: str
    purpose: str
    usb_path_hint: str
    role: SdrRole
    capabilities: list[str] = []
    bandwidth_hz: int = 0
    supported_roles: list[SdrRole] = []


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
        name="BladeRF 2.0 micro",
        purpose="Wideband SDR (Nuand bladeRF, 47 MHz–6 GHz, up to 56 MHz BW)",
        usb_path_hint="/dev/bus/usb",
    ),
    DeviceProfile(
        name="AntSDR E200",
        purpose="Wideband SDR (MicroPhase AntSDR E200, AD9361 / Zynq, 70 MHz–6 GHz)",
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
# the PATCH /api/v1/sdr/assign endpoint, but only to a role in the device's
# ``supported_roles`` list — hardware bandwidth makes the constraint hard:
#
#   * RTL-SDR V4 has ~2.4 MHz BW so it can ONLY serve as ``action`` (voice).
#     It is too slow at sweep and too narrow to decode 6 MHz NTSC analog FPV.
#   * HackRF One (~20 MHz BW) can sweep or carry ATV — scout or aux.
#   * BladeRF 2.0 micro xA4/xA9 (~40-56 MHz BW) — scout, action, or aux.
#   * AntSDR E200 (~56 MHz BW) — scout, action, or aux.
#
# Three radios are listed by default; the secondary HackRF defaults to
# ``scout`` rather than ``aux`` because a 56 MHz-class wideband front-end is
# wasted on follow-up duty when sweep performance benefits more from the
# extra spectrum coverage.  BladeRF / AntSDR variants are pre-registered
# below the defaults so operators with that hardware can be reassigned to
# whichever role suits their mission without code changes.
SDR_DEVICES: list[SdrDeviceConfig] = [
    SdrDeviceConfig(
        name="HackRF One",
        purpose="Wideband scout: sweep/recon, DJI DroneID, analog FPV (HF–6 GHz)",
        usb_path_hint="/dev/bus/usb",
        role="scout",
        capabilities=["wideband_sweep", "analog_fpv", "droneid"],
        bandwidth_hz=20_000_000,
        supported_roles=["scout", "aux"],
    ),
    SdrDeviceConfig(
        name="RTL-SDR V4",
        purpose="Action radio: VHF/UHF voice monitoring and AI transcription (P25/DMR)",
        usb_path_hint="/dev/bus/usb",
        role="action",
        capabilities=["vhf_voice", "uhf_voice", "p25", "dmr", "transcription"],
        bandwidth_hz=2_400_000,
        supported_roles=["action"],
    ),
    SdrDeviceConfig(
        name="HackRF One (Aux)",
        purpose="Wideband secondary: scout the other half of the band or decode analog FPV",
        usb_path_hint="/dev/bus/usb",
        role="scout",
        capabilities=["wideband_sweep", "analog_fpv", "atv", "droneid"],
        bandwidth_hz=20_000_000,
        supported_roles=["scout", "aux"],
    ),
    # ---- Alternate wideband radios — registered but not assigned a role
    # by default.  An operator running one of these instead of (or in
    # addition to) the HackRF can swap roles via PATCH /api/v1/sdr/assign.
    SdrDeviceConfig(
        name="BladeRF 2.0 micro",
        purpose="Wideband SDR (Nuand bladeRF): scout, FPV/ATV decode, or voice (47 MHz–6 GHz)",
        usb_path_hint="/dev/bus/usb",
        role="scout",
        capabilities=["wideband_sweep", "analog_fpv", "atv", "droneid", "vhf_voice", "uhf_voice"],
        bandwidth_hz=56_000_000,
        supported_roles=["scout", "action", "aux"],
    ),
    SdrDeviceConfig(
        name="AntSDR E200",
        purpose="Wideband SDR (MicroPhase AntSDR, AD9361/Zynq): scout, FPV/ATV, or voice (70 MHz–6 GHz)",
        usb_path_hint="/dev/bus/usb",
        role="scout",
        capabilities=["wideband_sweep", "analog_fpv", "atv", "droneid", "vhf_voice", "uhf_voice"],
        bandwidth_hz=56_000_000,
        supported_roles=["scout", "action", "aux"],
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
