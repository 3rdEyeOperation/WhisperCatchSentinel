"""Cursor-on-Target (CoT) builders and multicast emitter for the ATAK gateway."""
from __future__ import annotations

import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from .cuas import DroneContact


# TAK identity schemas used by the WhisperCatch Sentinel gateway. The
# problem statement explicitly calls out ``a-f-G`` for hostile/suspect
# UAS so the broadcast unmistakably renders as a hostile ground-friendly
# style marker for downstream operator tablets.
COT_TYPE_HOSTILE_UAS = "a-f-G"
COT_TYPE_PILOT = "a-h-G-U-C"


def _build_event(
    *,
    uid: str,
    cot_type: str,
    lat: float,
    lon: float,
    hae: float,
    callsign: str | None = None,
    remarks: str | None = None,
    stale_seconds: int = 120,
) -> str:
    now = datetime.now(timezone.utc)
    stale = now + timedelta(seconds=stale_seconds)
    event = ET.Element(
        "event",
        {
            "version": "2.0",
            "uid": uid,
            "type": cot_type,
            "time": now.isoformat(),
            "start": now.isoformat(),
            "stale": stale.isoformat(),
            "how": "m-g",
        },
    )
    ET.SubElement(
        event,
        "point",
        {
            "lat": str(lat),
            "lon": str(lon),
            "hae": str(hae),
            "ce": "10",
            "le": "10",
        },
    )
    detail = ET.SubElement(event, "detail")
    if callsign:
        ET.SubElement(detail, "contact", {"callsign": callsign})
    if remarks:
        rem = ET.SubElement(detail, "remarks")
        rem.text = remarks
    return ET.tostring(event, encoding="unicode")


def build_cot_event(uid: str, lat: float, lon: float, hae: float = 0.0) -> str:
    """Backwards-compatible CoT generator used by the test endpoint."""
    return _build_event(uid=uid, cot_type=COT_TYPE_HOSTILE_UAS, lat=lat, lon=lon, hae=hae)


def build_drone_events(contact: DroneContact) -> list[str]:
    """Generate per-contact CoT XML strings (drone + optional pilot)."""
    events: list[str] = []
    if contact.drone_lat is not None and contact.drone_lon is not None:
        events.append(
            _build_event(
                uid=f"wcs-drone-{(contact.serial or contact.mac or contact.uid or 'unknown')}",
                cot_type=COT_TYPE_HOSTILE_UAS,
                lat=contact.drone_lat,
                lon=contact.drone_lon,
                hae=contact.drone_alt_m or 0.0,
                callsign=contact.airframe or contact.protocol,
                remarks=f"protocol={contact.protocol};rssi={contact.rssi_dbm:.1f}dBm",
            )
        )
    if contact.pilot_lat is not None and contact.pilot_lon is not None:
        events.append(
            _build_event(
                uid=f"wcs-pilot-{(contact.serial or contact.mac or contact.uid or 'unknown')}",
                cot_type=COT_TYPE_PILOT,
                lat=contact.pilot_lat,
                lon=contact.pilot_lon,
                hae=0.0,
                callsign="Pilot",
                remarks=f"airframe={contact.airframe or 'unknown'}",
            )
        )
    return events


def multicast_cot(group: str, port: int, payload: str) -> None:
    data = payload.encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.sendto(data, (group, port))


class CotGateway:
    """Helper that turns a stream of :class:`DroneContact` into multicast CoT."""

    def __init__(self, group: str, port: int, *, sender=multicast_cot) -> None:
        self._group = group
        self._port = port
        self._sender = sender

    def broadcast(self, contact: DroneContact) -> list[str]:
        events = build_drone_events(contact)
        for event in events:
            self._sender(self._group, self._port, event)
        return events
