"""gpsd integration for the sensor node.

The sensor box is expected to have a USB / UART GPS receiver feeding the
``gpsd`` daemon (the standard Linux GPS multiplexer). This module provides a
**dependency-free** TCP client that speaks the gpsd JSON protocol so the
heatmap engine can stamp every observation with the live sensor location
instead of relying on a static, hand-configured coordinate.

Why no ``gps`` (gpsd-clients) Python dependency?
    * Adds a system-level build dependency that doesn't always have wheels.
    * The wire protocol is line-delimited JSON and is trivial to implement
      with the stdlib; this keeps the sensor image small and air-gappable.
    * We can unit-test the parser by pointing it at a localhost socket that
      replays canned gpsd frames — no need to mock C bindings.

References
    https://gpsd.io/gpsd_json.html — TPV, SKY, VERSION, WATCH messages.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Iterator


LOGGER = logging.getLogger(__name__)

# gpsd ships line-delimited JSON; messages are bounded by '\n'. 8 KB is far
# more than any real TPV/SKY frame so a single recv almost always returns a
# whole message — the iterator below handles fragmentation regardless.
_RECV_CHUNK = 8192

# gpsd ``mode`` field: 0/1 = no fix, 2 = 2D, 3 = 3D. Anything < 2 is unusable
# for plotting because there is no lat/lon yet.
_MIN_USABLE_MODE = 2

DEFAULT_GPSD_HOST = "127.0.0.1"
DEFAULT_GPSD_PORT = 2947


@dataclass(frozen=True)
class GpsFix:
    """A single GPS fix decoded from a gpsd TPV frame."""

    lat: float
    lon: float
    altitude_m: float | None
    mode: int
    received_at: float
    fix_time: str | None = None
    speed_mps: float | None = None
    track_deg: float | None = None
    eph_m: float | None = None  # horizontal position error estimate
    device: str | None = None

    @property
    def has_3d(self) -> bool:
        return self.mode >= 3

    def to_dict(self) -> dict:
        return {
            "lat": self.lat,
            "lon": self.lon,
            "altitude_m": self.altitude_m,
            "mode": self.mode,
            "received_at": self.received_at,
            "fix_time": self.fix_time,
            "speed_mps": self.speed_mps,
            "track_deg": self.track_deg,
            "eph_m": self.eph_m,
            "device": self.device,
        }


def parse_tpv(payload: dict) -> GpsFix | None:
    """Convert a parsed gpsd TPV JSON dict into a ``GpsFix``.

    Returns ``None`` for frames that are not TPV, that report no fix, or that
    lack the lat/lon required to plot the sensor on a heatmap.
    """
    if payload.get("class") != "TPV":
        return None
    mode = int(payload.get("mode", 0) or 0)
    if mode < _MIN_USABLE_MODE:
        return None
    lat = payload.get("lat")
    lon = payload.get("lon")
    if lat is None or lon is None:
        return None
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError):
        return None
    # altHAE (height above ellipsoid) is preferred when present; altMSL or the
    # legacy alt field are accepted as fallbacks. None is fine; the heatmap
    # only needs lat/lon.
    alt = payload.get("altHAE")
    if alt is None:
        alt = payload.get("altMSL", payload.get("alt"))
    try:
        alt_f = float(alt) if alt is not None else None
    except (TypeError, ValueError):
        alt_f = None
    return GpsFix(
        lat=lat_f,
        lon=lon_f,
        altitude_m=alt_f,
        mode=mode,
        received_at=time.time(),
        fix_time=payload.get("time"),
        speed_mps=_maybe_float(payload.get("speed")),
        track_deg=_maybe_float(payload.get("track")),
        eph_m=_maybe_float(payload.get("eph")),
        device=payload.get("device"),
    )


def _maybe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def iter_gpsd_lines(sock: socket.socket) -> Iterator[bytes]:
    """Yield line-delimited frames from a gpsd TCP socket.

    Exits cleanly when the peer closes the connection. The buffer is bounded
    by reality (gpsd never emits multi-MB frames), so no explicit cap is
    enforced here — overall safety is provided by the surrounding receiver
    loop which can be stopped via ``GpsReceiver.stop``.
    """
    buffer = bytearray()
    while True:
        chunk = sock.recv(_RECV_CHUNK)
        if not chunk:
            return
        buffer.extend(chunk)
        while True:
            nl = buffer.find(b"\n")
            if nl < 0:
                break
            line = bytes(buffer[:nl])
            del buffer[: nl + 1]
            if line:
                yield line


class GpsdClient:
    """Synchronous one-shot reader for the gpsd JSON protocol.

    Use this for tests, ad-hoc probes, and to populate a single fix; the
    streaming :class:`GpsReceiver` wraps it for long-running operation.
    """

    def __init__(
        self,
        host: str = DEFAULT_GPSD_HOST,
        port: int = DEFAULT_GPSD_PORT,
        *,
        connect_timeout: float = 2.0,
        read_timeout: float = 2.0,
    ) -> None:
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout

    def _connect(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port), self.connect_timeout)
        sock.settimeout(self.read_timeout)
        # Ask gpsd to stream JSON TPV/SKY frames for every device it owns.
        sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
        return sock

    def read_fix(self, *, max_frames: int = 50) -> GpsFix | None:
        """Connect, request a JSON watch, and return the first usable fix.

        Bounded by ``max_frames`` so a misbehaving daemon that only emits
        SKY / DEVICE frames can't deadlock the caller.
        """
        try:
            with self._connect() as sock:
                count = 0
                for raw in iter_gpsd_lines(sock):
                    count += 1
                    if count > max_frames:
                        return None
                    fix = self._decode(raw)
                    if fix is not None:
                        return fix
        except (OSError, socket.timeout) as exc:
            LOGGER.debug("gpsd read_fix failed: %s", exc)
            return None
        return None

    @staticmethod
    def _decode(raw: bytes) -> GpsFix | None:
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        return parse_tpv(payload)


class GpsReceiver:
    """Background thread that keeps the latest usable gpsd TPV fix in memory.

    The sensor backend treats the receiver as an optional dependency: when
    gpsd is unreachable (no GPS plugged in, daemon down, sensor running on a
    laptop in the office), :meth:`latest_fix` simply returns ``None`` and
    callers fall back to operator-supplied coordinates.

    The receiver is *not* started in the default factory — operators opt in
    by calling :meth:`start` after the API process is up. This keeps unit
    tests deterministic and avoids spawning daemon threads that pytest would
    have to chase around.
    """

    def __init__(
        self,
        host: str = DEFAULT_GPSD_HOST,
        port: int = DEFAULT_GPSD_PORT,
        *,
        reconnect_delay_s: float = 5.0,
        fix_ttl_s: float = 30.0,
        client_factory=None,
    ) -> None:
        self.host = host
        self.port = port
        self.reconnect_delay_s = reconnect_delay_s
        # A fix older than ``fix_ttl_s`` is considered stale (receiver lost
        # the constellation, antenna unplugged, etc.) and stops being served
        # to the rest of the pipeline so we never plot rotten coordinates.
        self.fix_ttl_s = fix_ttl_s
        self._client_factory = client_factory or (lambda: GpsdClient(host, port))
        self._lock = threading.Lock()
        self._fix: GpsFix | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="whispercatch-gpsd",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float | None = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------
    def set_fix(self, fix: GpsFix | None) -> None:
        """Inject a fix directly (used by tests and operator override)."""
        with self._lock:
            self._fix = fix

    def latest_fix(self) -> GpsFix | None:
        """Return the freshest non-stale fix, or ``None``."""
        with self._lock:
            fix = self._fix
        if fix is None:
            return None
        if (time.time() - fix.received_at) > self.fix_ttl_s:
            return None
        return fix

    @property
    def running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------
    def _run(self) -> None:  # pragma: no cover - exercised via integration
        while not self._stop.is_set():
            client = self._client_factory()
            try:
                with socket.create_connection(
                    (client.host, client.port), client.connect_timeout
                ) as sock:
                    sock.settimeout(client.read_timeout)
                    sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
                    for raw in iter_gpsd_lines(sock):
                        if self._stop.is_set():
                            return
                        fix = GpsdClient._decode(raw)
                        if fix is not None:
                            self.set_fix(fix)
            except (OSError, socket.timeout) as exc:
                LOGGER.debug("gpsd receiver disconnected: %s", exc)
            # Sleep with cooperative cancellation so ``stop()`` is snappy.
            if self._stop.wait(self.reconnect_delay_s):
                return
