import json
import os
from pathlib import Path

import pytest

from whispercatch_sentinel.keys import VolatileKeyVault


def test_inject_and_lookup_round_trip(tmp_path: Path) -> None:
    vault_path = tmp_path / "keys.json"
    vault = VolatileKeyVault(vault_path, enforce_tmpfs=False)

    meta = vault.inject("kid1", "AES-256-OFB", "00" * 32)
    assert meta.key_id == "kid1"
    assert meta.algorithm == "AES-256-OFB"

    material = vault.lookup("kid1")
    assert material is not None
    assert material.key_hex == "00" * 32

    metadata_list = vault.list_metadata()
    assert [m.key_id for m in metadata_list] == ["kid1"]


def test_rejects_invalid_inputs(tmp_path: Path) -> None:
    vault = VolatileKeyVault(tmp_path / "keys.json", enforce_tmpfs=False)

    with pytest.raises(ValueError):
        vault.inject("kid1", "AES-256-OFB", "00")  # wrong length
    with pytest.raises(ValueError):
        vault.inject("kid1", "AES-128-CBC", "00" * 16)  # unsupported alg
    with pytest.raises(ValueError):
        vault.inject("kid1", "DES-OFB", "zz" * 8)  # not hex
    with pytest.raises(ValueError):
        vault.inject(" ", "DES-OFB", "11" * 8)


def test_revoke_and_purge(tmp_path: Path) -> None:
    vault = VolatileKeyVault(tmp_path / "keys.json", enforce_tmpfs=False)
    vault.inject("a", "DES-OFB", "11" * 8)
    vault.inject("b", "DES-OFB", "22" * 8)

    assert vault.revoke("a") is True
    assert vault.revoke("a") is False
    assert vault.purge() == 1
    assert vault.list_metadata() == []


def test_refuses_non_tmpfs_when_enforced(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        VolatileKeyVault(tmp_path / "keys.json", enforce_tmpfs=True)


def test_file_is_user_only_perms(tmp_path: Path) -> None:
    vault_path = tmp_path / "keys.json"
    vault = VolatileKeyVault(vault_path, enforce_tmpfs=False)
    vault.inject("kid", "DES-OFB", "aa" * 8)

    mode = vault_path.stat().st_mode & 0o777
    assert mode == 0o600

    on_disk = json.loads(vault_path.read_text())
    assert on_disk == {"kid": {"algorithm": "DES-OFB", "key_hex": "aa" * 8}}
