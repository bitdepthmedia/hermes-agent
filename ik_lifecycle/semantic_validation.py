"""Semantic continuity gates; a file copy alone is never proof."""

from __future__ import annotations

from dataclasses import dataclass

from .sqlite_backup import DatabaseBackupReceipt


@dataclass(frozen=True)
class ContinuityCases:
    required_ids: tuple[str, ...]


@dataclass(frozen=True)
class SemanticGateSet:
    status: str
    codes: tuple[str, ...]


def validate_semantics(before: DatabaseBackupReceipt, after: DatabaseBackupReceipt, cases: ContinuityCases) -> SemanticGateSet:
    failures: list[str] = []
    if before.integrity_check != "ok" or after.integrity_check != "ok": failures.append("integrity")
    if before.foreign_key_violations or after.foreign_key_violations: failures.append("foreign-keys")
    if before.user_version != after.user_version: failures.append("schema-version")
    if before.row_counts != after.row_counts: failures.append("row-counts")
    if before.id_digest != after.id_digest: failures.append("stable-ids")
    if cases.required_ids and not before.id_digest: failures.append("required-ids")
    return SemanticGateSet("BLOCKED" if failures else "CLEAR", tuple(failures))
