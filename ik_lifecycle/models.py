"""Value objects shared by the Hermes staged lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping


class LifecycleBlockedError(RuntimeError):
    """A fail-closed lifecycle decision with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class StableRelease:
    tag: str
    commit_sha: str
    published_at: datetime
    html_url: str


@dataclass(frozen=True)
class ReleaseSelection:
    latest: StableRelease
    target: StableRelease
    discovered_at: datetime


@dataclass(frozen=True)
class RemoteContractResult:
    status: str
    code: str
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class LifecycleReceipt:
    kind: str
    status: str
    observed_at: datetime
    data: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = 1


@dataclass(frozen=True)
class CellSpec:
    """Non-secret lifecycle inputs for one independently promoted cell."""

    cell_id: str
    trust_zone: str
    protected_paths: tuple[Path, ...] = ()
    legacy_health_automation_status: str = "UNKNOWN"
    computer_history_path_status: str = "approval_required"


@dataclass(frozen=True)
class GateSet:
    """Evidence gates required before a static candidate can be sealed."""

    static_scan_clear: bool
    source_identity_clear: bool
    tests_clear: bool
    hooks_reviewed: bool
    dependency_install_clear: bool
    rollback_release_pointer: Path | None = None
    rollback_profile_pointer: Path | None = None
    dependency_approval_receipt: Path | None = None
    dependency_install_receipt: Path | None = None
