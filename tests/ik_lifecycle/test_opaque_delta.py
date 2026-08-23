from __future__ import annotations

from dataclasses import replace

import pytest

from ik_lifecycle.opaque_backup import OpaqueBackupReceipt
from ik_lifecycle.opaque_delta import bind_opaque_delta


def _receipt(snapshot: str, tree: str, count: int = 4, size: int = 100) -> OpaqueBackupReceipt:
    return OpaqueBackupReceipt(
        schema_version="ik.opaque-continuity-receipt.v1",
        snapshot_id=snapshot,
        source_alias="ernie-live-private-cell",
        storage_alias="local-encrypted-continuity",
        source_path_sha256="a" * 64,
        storage_attestation_sha256="b" * 64,
        created_at="2026-08-23T00:00:00Z",
        aggregate_file_count=count,
        aggregate_bytes=size,
        snapshot_tree_sha256=tree,
        clone_tree_sha256=tree,
        archive_sha256="c" * 64,
        archive_hmac_sha256="d" * 64,
        receipt_hmac_sha256="e" * 64,
        encryption="AES-256-CBC-PBKDF2-SHA256-HMAC-SHA256+FileVault",
        permission_state="dirs-0700-files-0600-backup-sealed",
        rollback_handle="rollback-handle",
        status="CLEAR",
    )


def test_binds_two_full_opaque_snapshots_without_names_or_paths() -> None:
    receipt = bind_opaque_delta(_receipt("base", "1" * 64), _receipt("final", "2" * 64, 6, 140))

    assert receipt.status == "CLEAR_BOUNDED_OPAQUE_DELTA"
    assert receipt.changed is True
    assert receipt.aggregate_file_count_delta == 2
    assert receipt.aggregate_bytes_delta == 40
    assert "path" not in str(receipt.to_dict()).lower()
    assert "filename" not in str(receipt.to_dict()).lower()


def test_delta_fails_closed_on_alias_source_storage_or_integrity_drift() -> None:
    base = _receipt("base", "1" * 64)
    for bad in (
        replace(_receipt("final", "2" * 64), source_alias="other"),
        replace(_receipt("final", "2" * 64), source_path_sha256="f" * 64),
        replace(_receipt("final", "2" * 64), storage_attestation_sha256="f" * 64),
        replace(_receipt("final", "2" * 64), clone_tree_sha256="3" * 64),
        replace(_receipt("final", "2" * 64), status="BLOCKED"),
    ):
        with pytest.raises(ValueError):
            bind_opaque_delta(base, bad)
