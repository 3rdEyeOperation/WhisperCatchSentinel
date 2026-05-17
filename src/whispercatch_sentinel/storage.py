"""SQLite persistence for non-sensitive WhisperCatch Sentinel state.

RED/BLACK isolation rule: this module **must never** persist cryptographic
keys or any other key material. Only clear-data telemetry, configuration
snapshots and transcript metadata may be stored here. Keys live exclusively
in :mod:`whispercatch_sentinel.keys`, backed by tmpfs.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS transcripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at REAL NOT NULL,
    talkgroup TEXT,
    encrypted INTEGER NOT NULL DEFAULT 0,
    decrypted INTEGER NOT NULL DEFAULT 0,
    algorithm TEXT,
    key_id TEXT,
    text TEXT NOT NULL,
    language TEXT
);
CREATE INDEX IF NOT EXISTS idx_transcripts_tg ON transcripts(talkgroup);
CREATE INDEX IF NOT EXISTS idx_transcripts_dec ON transcripts(decrypted);
CREATE TABLE IF NOT EXISTS heatmap_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    captured_at REAL NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    frequency_hz REAL NOT NULL,
    rssi_dbm REAL NOT NULL,
    signal_type TEXT NOT NULL,
    intensity REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_heatmap_signal ON heatmap_points(signal_type, frequency_hz);
"""


@dataclass(frozen=True)
class TranscriptRecord:
    captured_at: float
    text: str
    talkgroup: str | None = None
    encrypted: bool = False
    decrypted: bool = False
    algorithm: str | None = None
    key_id: str | None = None
    language: str | None = None


@dataclass(frozen=True)
class HeatmapRecord:
    captured_at: float
    lat: float
    lon: float
    frequency_hz: float
    rssi_dbm: float
    signal_type: str
    intensity: float


class Storage:
    """Thread-safe SQLite wrapper with explicit schema and small surface."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            isolation_level=None,  # autocommit; we wrap explicit transactions
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                yield self._conn
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    # --- Config -----------------------------------------------------------------
    def set_config(self, key: str, value: Any, now: float) -> None:
        payload = json.dumps(value, sort_keys=True)
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO config(key, value, updated_at) VALUES(?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, payload, now),
            )

    def get_config(self, key: str) -> Any | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM config WHERE key = ?", (key,)
            ).fetchone()
        return json.loads(row["value"]) if row else None

    def all_configs(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM config ORDER BY key"
            ).fetchall()
        return {row["key"]: json.loads(row["value"]) for row in rows}

    # --- Transcripts ------------------------------------------------------------
    def add_transcript(self, record: TranscriptRecord) -> int:
        data = asdict(record)
        data["encrypted"] = 1 if record.encrypted else 0
        data["decrypted"] = 1 if record.decrypted else 0
        with self._tx() as conn:
            cur = conn.execute(
                "INSERT INTO transcripts(captured_at, talkgroup, encrypted, decrypted, "
                "algorithm, key_id, text, language) "
                "VALUES(:captured_at, :talkgroup, :encrypted, :decrypted, :algorithm, "
                ":key_id, :text, :language)",
                data,
            )
            return int(cur.lastrowid)

    def query_transcripts(
        self,
        *,
        talkgroup: str | None = None,
        decrypted: bool | None = None,
        clear: bool | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if talkgroup is not None:
            clauses.append("talkgroup = ?")
            params.append(talkgroup)
        if decrypted is not None:
            clauses.append("decrypted = ?")
            params.append(1 if decrypted else 0)
        if clear is not None:
            clauses.append("encrypted = ?")
            params.append(0 if clear else 1)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT id, captured_at, talkgroup, encrypted, decrypted, algorithm, key_id, "
            f"text, language FROM transcripts {where} ORDER BY captured_at DESC LIMIT ?"
        )
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                **dict(row),
                "encrypted": bool(row["encrypted"]),
                "decrypted": bool(row["decrypted"]),
            }
            for row in rows
        ]

    # --- Heatmap ----------------------------------------------------------------
    def add_heatmap_points(self, records: Iterable[HeatmapRecord]) -> int:
        rows = [asdict(r) for r in records]
        if not rows:
            return 0
        with self._tx() as conn:
            conn.executemany(
                "INSERT INTO heatmap_points(captured_at, lat, lon, frequency_hz, "
                "rssi_dbm, signal_type, intensity) VALUES(:captured_at, :lat, :lon, "
                ":frequency_hz, :rssi_dbm, :signal_type, :intensity)",
                rows,
            )
        return len(rows)

    def query_heatmap(
        self,
        *,
        signal_type: str | None = None,
        frequency_hz: float | None = None,
        tolerance_hz: float = 5_000_000.0,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if signal_type is not None:
            clauses.append("signal_type = ?")
            params.append(signal_type)
        if frequency_hz is not None:
            clauses.append("frequency_hz BETWEEN ? AND ?")
            params.extend([frequency_hz - tolerance_hz, frequency_hz + tolerance_hz])
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT captured_at, lat, lon, frequency_hz, rssi_dbm, signal_type, intensity "
            f"FROM heatmap_points {where} ORDER BY captured_at DESC LIMIT ?"
        )
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
