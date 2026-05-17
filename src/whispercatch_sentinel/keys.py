"""Volatile RAM-disk key vault for SIGINT cryptographic material.

This module enforces the RED/BLACK boundary: all key data is held strictly
in a tmpfs-backed JSON file (default ``/mnt/ramdisk/keys.json``). Keys must
never be returned through telemetry endpoints; only metadata
(``key_id``, ``algorithm``) ever leaves this module.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .config import is_tmpfs_ramdisk


SUPPORTED_ALGORITHMS = frozenset({"AES-256-OFB", "DES-OFB"})


@dataclass(frozen=True)
class KeyMetadata:
    """Public-safe metadata describing a key without exposing key bytes."""

    key_id: str
    algorithm: str


@dataclass(frozen=True)
class KeyMaterial:
    """RED data — must remain inside the vault."""

    key_id: str
    algorithm: str
    key_hex: str


def _validate_algorithm(algorithm: str) -> None:
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise ValueError(
            f"unsupported algorithm '{algorithm}'; expected one of "
            f"{sorted(SUPPORTED_ALGORITHMS)}"
        )


def _validate_key_hex(algorithm: str, key_hex: str) -> bytes:
    try:
        raw = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise ValueError("key_hex must be a hexadecimal string") from exc
    expected = {"AES-256-OFB": 32, "DES-OFB": 8}[algorithm]
    if len(raw) != expected:
        raise ValueError(
            f"{algorithm} requires {expected}-byte key, got {len(raw)} bytes"
        )
    return raw


class VolatileKeyVault:
    """JSON-backed key store that refuses to operate off a tmpfs path.

    Parameters
    ----------
    path:
        Filesystem location of the keystore. By default keys are stored at
        ``/mnt/ramdisk/keys.json`` per the WhisperCatch Sentinel spec.
    enforce_tmpfs:
        If ``True`` (default), the parent directory must be tmpfs. Tests
        and offline tooling may disable this check.
    """

    DEFAULT_PATH = "/mnt/ramdisk/keys.json"

    def __init__(self, path: str | os.PathLike[str] = DEFAULT_PATH, *, enforce_tmpfs: bool = True) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        if enforce_tmpfs and not is_tmpfs_ramdisk(str(self._path.parent)):
            raise RuntimeError(
                f"refusing to use non-tmpfs key vault at {self._path.parent}; "
                "mount a tmpfs RAM-disk before storing key material"
            )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._write({})

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            return json.loads(self._path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        # 0o600 — read/write owner only; tmpfs is non-persistent but locked
        # down anyway to limit exposure to local processes.
        fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps(data).encode("utf-8"))
        finally:
            os.close(fd)

    def inject(self, key_id: str, algorithm: str, key_hex: str) -> KeyMetadata:
        """Store a key. Returns metadata only — never the secret material."""
        if not key_id or not key_id.strip():
            raise ValueError("key_id must be a non-empty string")
        _validate_algorithm(algorithm)
        _validate_key_hex(algorithm, key_hex)
        with self._lock:
            data = self._read()
            data[key_id] = {"algorithm": algorithm, "key_hex": key_hex}
            self._write(data)
        return KeyMetadata(key_id=key_id, algorithm=algorithm)

    def revoke(self, key_id: str) -> bool:
        with self._lock:
            data = self._read()
            if key_id in data:
                del data[key_id]
                self._write(data)
                return True
        return False

    def list_metadata(self) -> list[KeyMetadata]:
        with self._lock:
            data = self._read()
        return [
            KeyMetadata(key_id=k, algorithm=v.get("algorithm", "?"))
            for k, v in sorted(data.items())
        ]

    def lookup(self, key_id: str) -> KeyMaterial | None:
        """Retrieve full key material. Caller is responsible for keeping the
        returned object inside the decryption boundary."""
        with self._lock:
            data = self._read()
        entry = data.get(key_id)
        if not entry:
            return None
        return KeyMaterial(
            key_id=key_id,
            algorithm=entry["algorithm"],
            key_hex=entry["key_hex"],
        )

    def purge(self) -> int:
        with self._lock:
            data = self._read()
            count = len(data)
            self._write({})
        return count

    @property
    def tmpfs_dir(self) -> Path:
        """Directory holding the vault — useful for staging cleartext audio
        that must remain RAM-resident alongside the keys."""
        return self._path.parent

    def inject_many(self, entries: Iterable[dict[str, str]]) -> list[KeyMetadata]:
        results: list[KeyMetadata] = []
        for entry in entries:
            results.append(
                self.inject(entry["key_id"], entry["algorithm"], entry["key_hex"])
            )
        return results
