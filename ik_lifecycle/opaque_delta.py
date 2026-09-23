"""Aggregate-only binding between two independently sealed opaque snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .opaque_backup import OpaqueBackupReceipt


@dataclass(frozen=True)
class OpaqueDeltaReceipt:
    schema_id: str
    status: str
    base_snapshot_id: str
    final_snapshot_id: str
    base_tree_sha256: str
    final_tree_sha256: str
    base_archive_sha256: str
    final_archive_sha256: str
    changed: bool
    aggregate_file_count_delta: int
    aggregate_bytes_delta: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def bind_opaque_delta(base: OpaqueBackupReceipt, final: OpaqueBackupReceipt) -> OpaqueDeltaReceipt:
    """Bind two full encrypted snapshots; never enumerate their contents."""

    for receipt in (base, final):
        if (
            receipt.status != "CLEAR"
            or receipt.snapshot_tree_sha256 != receipt.clone_tree_sha256
            or len(receipt.archive_sha256) != 64
            or len(receipt.receipt_hmac_sha256) != 64
        ):
            raise ValueError("opaque snapshot integrity is not clear")
    if base.snapshot_id == final.snapshot_id:
        raise ValueError("final snapshot must be independently captured")
    if (
        base.source_alias != final.source_alias
        or base.storage_alias != final.storage_alias
        or base.source_path_sha256 != final.source_path_sha256
        or base.storage_attestation_sha256 != final.storage_attestation_sha256
        or base.encryption != final.encryption
    ):
        raise ValueError("opaque snapshot scope drifted")
    return OpaqueDeltaReceipt(
        schema_id="ik.hermes.opaque-delta.v1",
        status="CLEAR_BOUNDED_OPAQUE_DELTA",
        base_snapshot_id=base.snapshot_id,
        final_snapshot_id=final.snapshot_id,
        base_tree_sha256=base.snapshot_tree_sha256,
        final_tree_sha256=final.snapshot_tree_sha256,
        base_archive_sha256=base.archive_sha256,
        final_archive_sha256=final.archive_sha256,
        changed=base.snapshot_tree_sha256 != final.snapshot_tree_sha256,
        aggregate_file_count_delta=final.aggregate_file_count - base.aggregate_file_count,
        aggregate_bytes_delta=final.aggregate_bytes - base.aggregate_bytes,
    )
