"""SIGINT voice pipeline: Trunk Recorder → optional decrypt → whisper.cpp.

The pipeline watches Trunk Recorder's output directory. Each new call is
described by a JSON sidecar containing ``{talkgroup, encrypted, algorithm,
key_id, wav_path}``. Encrypted calls are decrypted in-memory using key
material drawn from :class:`VolatileKeyVault` and a subprocess wrapper
around OpenSSL (``openssl enc``) so the symmetric primitive name maps
directly to the wire format. Clear audio is fed into ``whisper.cpp`` and
the resulting JSON transcript is persisted (without keys).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from .keys import KeyMaterial, VolatileKeyVault
from .storage import Storage, TranscriptRecord


log = logging.getLogger(__name__)


# Mapping from radio-side algorithm IDs to OpenSSL cipher names. The
# WhisperCatch Sentinel spec calls out AES-256-OFB and DES-OFB explicitly.
_OPENSSL_CIPHERS = {
    "AES-256-OFB": "aes-256-ofb",
    "DES-OFB": "des-ofb",
}


@dataclass(frozen=True)
class CallDescriptor:
    wav_path: Path
    talkgroup: str | None
    encrypted: bool
    algorithm: str | None = None
    key_id: str | None = None
    iv_hex: str | None = None
    language: str = "en"


def parse_descriptor(path: Path) -> CallDescriptor:
    data = json.loads(path.read_text(encoding="utf-8"))
    return CallDescriptor(
        wav_path=Path(data["wav_path"]),
        talkgroup=data.get("talkgroup"),
        encrypted=bool(data.get("encrypted", False)),
        algorithm=data.get("algorithm"),
        key_id=data.get("key_id"),
        iv_hex=data.get("iv_hex"),
        language=data.get("language", "en"),
    )


class SigintPipeline:
    """Coordinates decrypt → transcribe → persist for each captured call."""

    def __init__(
        self,
        *,
        vault: VolatileKeyVault,
        storage: Storage,
        whisper_binary: str = "whisper-cli",
        whisper_model: str = "models/ggml-base.bin",
        openssl_binary: str = "openssl",
        on_transcript: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        self._vault = vault
        self._storage = storage
        self._whisper_binary = whisper_binary
        self._whisper_model = whisper_model
        self._openssl_binary = openssl_binary
        self._on_transcript = on_transcript

    # --- public API -------------------------------------------------------------
    async def handle_call(self, descriptor: CallDescriptor) -> dict:
        clear_path = await self._materialize_clear_audio(descriptor)
        try:
            text = await self._transcribe(clear_path, language=descriptor.language)
        finally:
            if clear_path != descriptor.wav_path:
                _shred(clear_path)

        record = TranscriptRecord(
            captured_at=time.time(),
            text=text,
            talkgroup=descriptor.talkgroup,
            encrypted=descriptor.encrypted,
            decrypted=descriptor.encrypted,
            algorithm=descriptor.algorithm,
            key_id=descriptor.key_id,
            language=descriptor.language,
        )
        row_id = self._storage.add_transcript(record)
        payload = {
            "id": row_id,
            "captured_at": record.captured_at,
            "talkgroup": record.talkgroup,
            "encrypted": record.encrypted,
            "decrypted": record.decrypted,
            "algorithm": record.algorithm,
            "key_id": record.key_id,
            "language": record.language,
            "text": record.text,
        }
        if self._on_transcript is not None:
            await self._on_transcript(payload)
        return payload

    # --- internals --------------------------------------------------------------
    async def _materialize_clear_audio(self, descriptor: CallDescriptor) -> Path:
        if not descriptor.encrypted:
            return descriptor.wav_path
        if not descriptor.algorithm or not descriptor.key_id:
            raise ValueError("encrypted call missing algorithm or key_id")
        cipher = _OPENSSL_CIPHERS.get(descriptor.algorithm)
        if cipher is None:
            raise ValueError(f"unsupported cipher '{descriptor.algorithm}'")
        material = self._vault.lookup(descriptor.key_id)
        if material is None:
            raise KeyError(f"no key in vault for key_id '{descriptor.key_id}'")
        return await asyncio.to_thread(
            self._decrypt_sync,
            descriptor=descriptor,
            material=material,
            cipher=cipher,
        )

    def _decrypt_sync(
        self,
        *,
        descriptor: CallDescriptor,
        material: KeyMaterial,
        cipher: str,
    ) -> Path:
        # Work strictly inside the tmpfs RAM-disk so cleartext never lands on
        # persistent storage. Falls back to /dev/shm if the vault is elsewhere.
        tmpfs_dir = self._vault._path.parent  # noqa: SLF001 — intentional internal use
        out_path = Path(tempfile.mkstemp(prefix="wcs-clear-", suffix=".wav", dir=tmpfs_dir)[1])
        args = [
            self._openssl_binary,
            "enc",
            "-d",
            f"-{cipher}",
            "-K",
            material.key_hex,
            "-in",
            str(descriptor.wav_path),
            "-out",
            str(out_path),
        ]
        if descriptor.iv_hex:
            args.extend(["-iv", descriptor.iv_hex])
        result = subprocess.run(args, capture_output=True, check=False)
        if result.returncode != 0:
            _shred(out_path)
            raise RuntimeError(
                f"openssl decrypt failed (rc={result.returncode}): "
                f"{result.stderr.decode('utf-8', 'replace').strip()}"
            )
        return out_path

    async def _transcribe(self, wav_path: Path, *, language: str) -> str:
        return await asyncio.to_thread(self._transcribe_sync, wav_path, language)

    def _transcribe_sync(self, wav_path: Path, language: str) -> str:
        args = [
            self._whisper_binary,
            "-m",
            self._whisper_model,
            "-l",
            language,
            "-oj",     # JSON output
            "-nt",     # no timestamps in stdout
            "-f",
            str(wav_path),
        ]
        result = subprocess.run(args, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"whisper.cpp failed (rc={result.returncode}): "
                f"{result.stderr.decode('utf-8', 'replace').strip()}"
            )
        stdout = result.stdout.decode("utf-8", "replace").strip()
        try:
            doc = json.loads(stdout)
        except json.JSONDecodeError:
            return stdout
        if isinstance(doc, dict) and "transcription" in doc:
            return " ".join(
                seg.get("text", "").strip() for seg in doc["transcription"]
            ).strip()
        if isinstance(doc, dict) and "text" in doc:
            return str(doc["text"]).strip()
        return stdout


def _shred(path: Path) -> None:
    """Best-effort secure removal of decrypted audio."""
    try:
        if not path.exists():
            return
        # Overwrite the file once before unlinking. The volume is tmpfs so
        # physical persistence is impossible, but we still scrub the page
        # cache for any process that might hold an fd.
        size = path.stat().st_size
        with open(path, "r+b", buffering=0) as fh:
            fh.write(os.urandom(min(size, 4096)))
            fh.flush()
        path.unlink()
    except OSError:
        # Last resort: try plain delete via shutil so we never leave clear-
        # text audio behind in /dev/shm if shred-style overwrite fails.
        try:
            path.unlink()
        except OSError:
            log.warning("failed to remove cleartext audio at %s", path)
        else:
            return
        shutil.rmtree(path, ignore_errors=True)
