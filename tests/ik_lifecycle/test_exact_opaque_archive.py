from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from ik_lifecycle.exact_opaque_archive import (
    ExactOpaqueArchiveError,
    ExactOpaqueArchiveRequest,
    build_exact_opaque_archive,
)


def _source(root: Path) -> tuple[Path, Path]:
    primary = root / "primary"
    fast = root / "fast"
    primary.mkdir(mode=0o700)
    fast.mkdir(mode=0o700)
    (primary / "shared.bin").write_bytes(b"private-synthetic")
    (primary / "shared.bin").chmod(0o600)
    (fast / "local.bin").write_bytes(b"fast-synthetic")
    (fast / "local.bin").chmod(0o600)
    (fast / "shared-link").symlink_to(primary / "shared.bin")
    return primary, fast


def test_exact_archive_preserves_declared_internal_link_and_encrypts_bytes(tmp_path: Path) -> None:
    primary, fast = _source(tmp_path)
    result = build_exact_opaque_archive(
        ExactOpaqueArchiveRequest(
            source_root=fast,
            allowed_link_root=primary,
            storage_root=tmp_path / "store",
            source_alias="ernie-fast-live",
            idempotency_key="snapshot-001",
        ),
        storage_clear=True,
    )

    assert result.receipt.status == "CLEAR"
    assert result.receipt.symlink_count == 1
    assert b"private-synthetic" not in result.archive_path.read_bytes()
    assert result.clone_root.joinpath("shared-link").is_symlink()
    assert os.readlink(result.clone_root / "shared-link") == str(primary / "shared.bin")
    assert set(result.receipt.to_dict()) == {
        "schema_id", "status", "snapshot_id", "source_alias", "aggregate_entry_count",
        "aggregate_bytes", "symlink_count", "tree_sha256", "clone_tree_sha256",
        "archive_sha256", "archive_hmac_sha256", "rollback_handle",
    }


def test_exact_archive_rejects_link_outside_declared_root_without_path_leak(tmp_path: Path) -> None:
    primary, fast = _source(tmp_path)
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    (fast / "shared-link").unlink()
    (fast / "shared-link").symlink_to(outside)
    request = ExactOpaqueArchiveRequest(fast, primary, tmp_path / "store", "ernie-fast-live", "snapshot-002")

    with pytest.raises(ExactOpaqueArchiveError, match="source_symlink_target_rejected") as raised:
        build_exact_opaque_archive(request, storage_clear=True)
    assert str(outside) not in str(raised.value)


def test_exact_archive_roundtrip_retains_link_and_regular_bytes(tmp_path: Path) -> None:
    primary, fast = _source(tmp_path)
    result = build_exact_opaque_archive(
        ExactOpaqueArchiveRequest(fast, primary, tmp_path / "store", "ernie-fast-live", "snapshot-003"),
        storage_clear=True,
    )
    tar_path = tmp_path / "decrypted.tar"
    restored = tmp_path / "restored"
    restored.mkdir(mode=0o700)
    subprocess.run(
        (
            "/usr/bin/openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "600000",
            "-md", "sha256", "-in", str(result.archive_path), "-out", str(tar_path),
            "-pass", f"file:{result.key_path}",
        ),
        check=True,
        capture_output=True,
    )
    subprocess.run(("/usr/bin/tar", "-xf", str(tar_path), "-C", str(restored)), check=True, capture_output=True)
    assert (restored / "local.bin").read_bytes() == b"fast-synthetic"
    assert (restored / "shared-link").is_symlink()
