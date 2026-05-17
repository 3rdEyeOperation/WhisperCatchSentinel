from __future__ import annotations

import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone


def build_cot_event(uid: str, lat: float, lon: float, hae: float = 0.0) -> str:
    now = datetime.now(timezone.utc)
    stale = now + timedelta(minutes=2)

    event = ET.Element(
        "event",
        {
            "version": "2.0",
            "uid": uid,
            "type": "a-f-A",
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

    return ET.tostring(event, encoding="unicode")


def multicast_cot(group: str, port: int, payload: str) -> None:
    data = payload.encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.sendto(data, (group, port))
    finally:
        sock.close()
