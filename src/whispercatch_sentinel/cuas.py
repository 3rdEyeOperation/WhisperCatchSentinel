"""C-UAS multi-source aggregation.

Ingests records from ``droneid-go`` (DJI DroneID via HackRF ZMQ), 802.11
Wi-Fi beacons, and Sniffle BLE Coded PHY taps, then produces a single
deduplicated list of tracked airframes for the ATAK plugin.

Deduplication key precedence (highest first):
    1. ``serial`` (Remote-ID / ASTM DRI serial number)
    2. ``mac`` (Wi-Fi Beacon BSSID or BLE peer address)
    3. ``uid``  (synthesized identifier from raw HackRF correlations)
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field, replace
from typing import Iterable


@dataclass(frozen=True)
class DroneContact:
    captured_at: float
    source: str            # "droneid", "wifi", "ble", "hackrf"
    protocol: str          # "OcuSync", "ASTM DRI", ...
    rssi_dbm: float
    airframe: str | None = None
    serial: str | None = None
    mac: str | None = None
    uid: str | None = None
    drone_lat: float | None = None
    drone_lon: float | None = None
    drone_alt_m: float | None = None
    pilot_lat: float | None = None
    pilot_lon: float | None = None
    home_lat: float | None = None
    home_lon: float | None = None


def contact_key(contact: DroneContact) -> str:
    if contact.serial:
        return f"serial:{contact.serial.lower()}"
    if contact.mac:
        return f"mac:{contact.mac.lower().replace('-', ':')}"
    if contact.uid:
        return f"uid:{contact.uid.lower()}"
    raise ValueError("DroneContact must carry one of serial/mac/uid for tracking")


def _merge(existing: DroneContact, incoming: DroneContact) -> DroneContact:
    """Merge an incoming contact into an existing aggregate.

    The merge keeps the most recent timestamp, prefers populated fields,
    and uses the strongest (highest, least negative) RSSI.
    """
    return DroneContact(
        captured_at=max(existing.captured_at, incoming.captured_at),
        source=incoming.source if incoming.captured_at >= existing.captured_at else existing.source,
        protocol=incoming.protocol or existing.protocol,
        rssi_dbm=max(existing.rssi_dbm, incoming.rssi_dbm),
        airframe=incoming.airframe or existing.airframe,
        serial=incoming.serial or existing.serial,
        mac=incoming.mac or existing.mac,
        uid=incoming.uid or existing.uid,
        drone_lat=incoming.drone_lat if incoming.drone_lat is not None else existing.drone_lat,
        drone_lon=incoming.drone_lon if incoming.drone_lon is not None else existing.drone_lon,
        drone_alt_m=incoming.drone_alt_m if incoming.drone_alt_m is not None else existing.drone_alt_m,
        pilot_lat=incoming.pilot_lat if incoming.pilot_lat is not None else existing.pilot_lat,
        pilot_lon=incoming.pilot_lon if incoming.pilot_lon is not None else existing.pilot_lon,
        home_lat=incoming.home_lat if incoming.home_lat is not None else existing.home_lat,
        home_lon=incoming.home_lon if incoming.home_lon is not None else existing.home_lon,
    )


@dataclass
class CuasAggregator:
    """Thread-safe in-memory aggregator with TTL-based eviction."""

    ttl_seconds: float = 90.0
    _contacts: dict[str, DroneContact] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def ingest(self, contact: DroneContact) -> DroneContact:
        key = contact_key(contact)
        with self._lock:
            existing = self._contacts.get(key)
            merged = _merge(existing, contact) if existing else contact
            self._contacts[key] = merged
            return merged

    def ingest_many(self, contacts: Iterable[DroneContact]) -> list[DroneContact]:
        return [self.ingest(c) for c in contacts]

    def prune(self, *, now: float | None = None) -> int:
        now = now if now is not None else time.time()
        cutoff = now - self.ttl_seconds
        with self._lock:
            stale = [k for k, c in self._contacts.items() if c.captured_at < cutoff]
            for k in stale:
                del self._contacts[k]
            return len(stale)

    def snapshot(self) -> list[DroneContact]:
        with self._lock:
            return sorted(
                self._contacts.values(),
                key=lambda c: c.captured_at,
                reverse=True,
            )

    def with_airframe(self, airframe: str) -> "CuasAggregator":
        """Convenience for tests: clone with overridden airframe defaults."""
        clone = CuasAggregator(ttl_seconds=self.ttl_seconds)
        with self._lock:
            for key, contact in self._contacts.items():
                clone._contacts[key] = replace(contact, airframe=airframe)
        return clone
