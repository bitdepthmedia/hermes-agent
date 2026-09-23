"""Disabled-by-default lifecycle evidence and drift classification.

This module contains no scheduler, service, promotion, restart, or live-system
adapter.  Callers must provide already-authorized, non-secret evidence readers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from .models import CellSpec, LifecycleBlockedError, ReleaseSelection


_HEX = frozenset("0123456789abcdef")


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _valid_hex(value: str, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and set(value.lower()) <= _HEX


@dataclass(frozen=True)
class CellLifecycleSnapshot:
    cell_id: str
    deployed_release: str
    profile_generation: int
    runtime_sha: str
    code_sha: str
    health: str
    approval_state: str
    promotion_state: str
    rollback_artifact_sha256: str
    receipt_digest: str
    promotion_blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateLifecycleSnapshot:
    candidate_id: str
    target_sha: str
    stage: str
    digest: str


@dataclass(frozen=True)
class LifecycleState:
    observed_at: datetime
    next_check_at: datetime
    latest_tag: str
    latest_sha: str
    target_tag: str
    target_sha: str
    cells: tuple[CellLifecycleSnapshot, ...]
    candidate: CandidateLifecycleSnapshot
    retired_updater_disabled: bool
    promotion_performed: bool
    schedule_active: bool
    receipt_digest: str


@dataclass(frozen=True)
class ExceptionDecision:
    tier: str
    notify: bool
    notification_key: str
    required_decision: str
    reason: str
    evidence_digest: str
    candidate_digest: str
    cell_id: str
    prepare_candidate: bool


@dataclass(frozen=True)
class CandidatePreparationResult:
    status: str
    candidate_digest: str
    promotion_performed: bool = False
    restart_performed: bool = False


@dataclass(frozen=True)
class MonitorTick:
    state: LifecycleState
    decision: ExceptionDecision
    preparation: CandidatePreparationResult


class ReleaseSource(Protocol):
    def discover(self) -> ReleaseSelection: ...
    def read_cell(self, cell: CellSpec) -> CellLifecycleSnapshot: ...
    def read_candidate(self, target_sha: str) -> CandidateLifecycleSnapshot: ...
    def retired_updater_disabled(self) -> bool: ...
    def observed_at(self) -> datetime: ...


def _validate_selection(selection: ReleaseSelection) -> None:
    if (
        selection.latest.tag == selection.target.tag
        or not _valid_hex(selection.latest.commit_sha, 40)
        or not _valid_hex(selection.target.commit_sha, 40)
        or selection.latest.published_at < selection.target.published_at
    ):
        raise LifecycleBlockedError("lifecycle_release_evidence_invalid", "release selection evidence is invalid")


def _validate_cell(cell: CellLifecycleSnapshot, expected_id: str) -> None:
    if (
        cell.cell_id != expected_id
        or not cell.deployed_release
        or not isinstance(cell.profile_generation, int)
        or cell.profile_generation < 0
        or not _valid_hex(cell.runtime_sha, 40)
        or not _valid_hex(cell.code_sha, 40)
        or cell.health not in {"CLEAR", "WARN", "BLOCKED", "CRITICAL"}
        or not cell.approval_state
        or not cell.promotion_state
        or not _valid_hex(cell.rollback_artifact_sha256, 64)
        or not _valid_hex(cell.receipt_digest, 64)
        or any(not blocker or "/" in blocker for blocker in cell.promotion_blockers)
    ):
        raise LifecycleBlockedError("lifecycle_cell_evidence_invalid", "cell lifecycle evidence is incomplete")


def collect_lifecycle_state(
    cells: Sequence[CellSpec],
    source: ReleaseSource,
    *,
    interval_minutes: int = 25,
) -> LifecycleState:
    """Collect redacted evidence only; never prepare, promote, restart, or schedule."""

    if not 1 <= interval_minutes <= 1440:
        raise LifecycleBlockedError("lifecycle_interval_invalid", "lifecycle interval is outside the supported range")
    if not cells or len({cell.cell_id for cell in cells}) != len(cells):
        raise LifecycleBlockedError("lifecycle_cell_set_invalid", "lifecycle cells must be unique")
    selection = source.discover()
    _validate_selection(selection)
    observed = source.observed_at()
    if observed.tzinfo is None:
        raise LifecycleBlockedError("lifecycle_time_invalid", "lifecycle evidence time must include a timezone")
    snapshots = []
    for cell in sorted(cells, key=lambda value: value.cell_id):
        snapshot = source.read_cell(cell)
        _validate_cell(snapshot, cell.cell_id)
        snapshots.append(snapshot)
    candidate = source.read_candidate(selection.target.commit_sha)
    if (
        not candidate.candidate_id
        or candidate.target_sha != selection.target.commit_sha
        or candidate.stage not in {"MISSING", "PREPARING", "FAILED", "TESTED", "SEALED", "READY_FOR_APPROVAL"}
        or not _valid_hex(candidate.digest, 64)
    ):
        raise LifecycleBlockedError("lifecycle_candidate_evidence_invalid", "candidate lifecycle evidence is incomplete")
    stable = {
        "latest_tag": selection.latest.tag,
        "latest_sha": selection.latest.commit_sha,
        "target_tag": selection.target.tag,
        "target_sha": selection.target.commit_sha,
        "cells": [asdict(item) for item in snapshots],
        "candidate": asdict(candidate),
        "retired_updater_disabled": bool(source.retired_updater_disabled()),
        "promotion_performed": False,
        "schedule_active": False,
    }
    return LifecycleState(
        observed_at=observed.astimezone(timezone.utc),
        next_check_at=observed.astimezone(timezone.utc) + timedelta(minutes=interval_minutes),
        cells=tuple(snapshots),
        candidate=candidate,
        receipt_digest=_digest(stable),
        **{key: value for key, value in stable.items() if key not in {"cells", "candidate"}},
    )


def _decision(state: LifecycleState, tier: str, required: str, reason: str, *, cell_id: str = "platform", prepare: bool = False) -> ExceptionDecision:
    key = _digest({"tier": tier, "required": required, "reason": reason, "evidence": state.receipt_digest})
    return ExceptionDecision(
        tier=tier,
        notify=tier != "CLEAR",
        notification_key=key,
        required_decision=required,
        reason=reason,
        evidence_digest=state.receipt_digest,
        candidate_digest=state.candidate.digest,
        cell_id=cell_id,
        prepare_candidate=prepare,
    )


def classify_exception(previous: LifecycleState | None, current: LifecycleState) -> ExceptionDecision:
    if not current.retired_updater_disabled:
        result = _decision(current, "CRITICAL", "disable_retired_in_place_updater", "retired_updater_enabled")
    else:
        parity = next((cell for cell in current.cells if cell.runtime_sha != cell.code_sha), None)
        unhealthy = next((cell for cell in current.cells if cell.health in {"BLOCKED", "CRITICAL"}), None)
        legacy = next((cell for cell in current.cells if "legacy_bert_health_automation_active" in cell.promotion_blockers), None)
        if parity is not None:
            result = _decision(current, "CRITICAL", "restore_runtime_code_parity", "runtime_code_parity_failed", cell_id=parity.cell_id)
        elif unhealthy is not None:
            result = _decision(current, unhealthy.health, "resolve_cell_health", "cell_health_not_clear", cell_id=unhealthy.cell_id)
        elif current.candidate.stage == "FAILED":
            result = _decision(current, "BLOCKED", "review_failed_candidate", "candidate_failed")
        elif current.candidate.stage == "READY_FOR_APPROVAL" and legacy is not None:
            result = _decision(
                current,
                "BLOCKED",
                "pause_legacy_bert_health_automation_before_final_promotion",
                "legacy_automation_overlap_risk",
                cell_id=legacy.cell_id,
            )
        elif current.candidate.stage == "READY_FOR_APPROVAL":
            result = _decision(current, "WARN", "review_candidate_promotion", "candidate_ready_for_separate_approval")
        elif any(cell.deployed_release != current.target_tag for cell in current.cells) and current.candidate.stage == "MISSING":
            result = _decision(current, "CLEAR", "none", "target_drift_background_preparation", prepare=True)
        else:
            result = _decision(current, "CLEAR", "none", "background_evidence_clear")
    if previous is not None and previous.receipt_digest == current.receipt_digest:
        return ExceptionDecision(**{**asdict(result), "notify": False})
    return result


def prepare_candidate_if_drifted(
    state: LifecycleState,
    preparer: Callable[[LifecycleState], Mapping[str, object]] | None = None,
) -> CandidatePreparationResult:
    """Request or invoke one injected preparation step; promotion is impossible here."""

    drifted = any(cell.deployed_release != state.target_tag for cell in state.cells)
    if not drifted or state.candidate.stage != "MISSING":
        return CandidatePreparationResult("NOOP", state.candidate.digest)
    if preparer is None:
        return CandidatePreparationResult("PREPARATION_REQUIRED", state.candidate.digest)
    output = preparer(state)
    digest = output.get("candidate_digest") if isinstance(output, Mapping) else None
    if not _valid_hex(digest, 64) or output.get("promotion_performed") or output.get("restart_performed"):
        raise LifecycleBlockedError("candidate_preparation_contract_invalid", "candidate preparation crossed its authority boundary")
    return CandidatePreparationResult("PREPARED", str(digest))


class LifecycleMonitor:
    """One local tick guarded by a nonblocking lock; disabled until called."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = Path(lock_path)

    def tick(self, cells: Sequence[CellSpec], source: ReleaseSource, *, previous: LifecycleState | None = None) -> MonitorTick:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a+b") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise LifecycleBlockedError("lifecycle_monitor_locked", "another lifecycle evidence tick is active") from error
            state = collect_lifecycle_state(cells, source)
            decision = classify_exception(previous, state)
            preparation = prepare_candidate_if_drifted(state)
            return MonitorTick(state, decision, preparation)
