from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import fcntl

import pytest

from ik_lifecycle.models import CellSpec, LifecycleBlockedError, ReleaseSelection, StableRelease
from ik_lifecycle.monitor import (
    CandidateLifecycleSnapshot,
    CellLifecycleSnapshot,
    LifecycleMonitor,
    classify_exception,
    collect_lifecycle_state,
    prepare_candidate_if_drifted,
)


NOW = datetime(2026, 8, 22, 17, 0, tzinfo=timezone.utc)


class StaticSource:
    def __init__(self) -> None:
        self.retired_disabled = True
        self.prepared = 0
        self.selection = ReleaseSelection(
            StableRelease("v2026.8.19", "f" * 40, NOW, "https://example.invalid/latest"),
            StableRelease("v2026.8.18", "e" * 40, NOW, "https://example.invalid/target"),
            NOW,
        )
        self.candidate = CandidateLifecycleSnapshot("candidate-v1", "e" * 40, "MISSING", "a" * 64)
        self.cells = {
            cell: CellLifecycleSnapshot(
                cell_id=cell,
                deployed_release="v2026.8.17",
                profile_generation=1,
                runtime_sha="d" * 40,
                code_sha="d" * 40,
                health="CLEAR",
                approval_state="NOT_REQUESTED",
                promotion_state="NOT_PROMOTED",
                rollback_artifact_sha256="b" * 64,
                receipt_digest="c" * 64,
                promotion_blockers=("legacy_bert_health_automation_active",) if cell == "bert" else (),
            )
            for cell in ("ernie", "bert")
        }

    def discover(self) -> ReleaseSelection:
        return self.selection

    def read_cell(self, cell: CellSpec) -> CellLifecycleSnapshot:
        return self.cells[cell.cell_id]

    def read_candidate(self, target_sha: str) -> CandidateLifecycleSnapshot:
        assert target_sha == "e" * 40
        return self.candidate

    def retired_updater_disabled(self) -> bool:
        return self.retired_disabled

    def observed_at(self) -> datetime:
        return NOW


def cells() -> tuple[CellSpec, ...]:
    return (CellSpec("ernie", "local-private"), CellSpec("bert", "sanitized-cloud"))


def test_collects_complete_two_cell_evidence_with_configurable_quiet_next_check() -> None:
    state = collect_lifecycle_state(cells(), StaticSource(), interval_minutes=25)

    assert (state.latest_tag, state.target_tag) == ("v2026.8.19", "v2026.8.18")
    assert state.next_check_at.isoformat() == "2026-08-22T17:25:00+00:00"
    assert [cell.cell_id for cell in state.cells] == ["bert", "ernie"]
    assert state.retired_updater_disabled is True
    assert state.promotion_performed is False
    assert len(state.receipt_digest) == 64


def test_drift_prepares_quietly_but_never_promotes_or_restarts() -> None:
    state = collect_lifecycle_state(cells(), StaticSource())
    decision = classify_exception(None, state)
    result = prepare_candidate_if_drifted(state, lambda _: {"candidate_digest": "9" * 64})

    assert decision.tier == "CLEAR"
    assert decision.notify is False
    assert decision.prepare_candidate is True
    assert result.status == "PREPARED"
    assert result.promotion_performed is False
    assert result.restart_performed is False


def test_unchanged_tick_suppresses_duplicate_notification() -> None:
    source = StaticSource()
    source.candidate = replace(source.candidate, stage="READY_FOR_APPROVAL")
    first = collect_lifecycle_state(cells(), source)
    previous_decision = classify_exception(None, first)
    repeated = collect_lifecycle_state(cells(), source)
    repeated_decision = classify_exception(first, repeated)

    assert previous_decision.tier == "BLOCKED"
    assert previous_decision.notify is True
    assert repeated_decision.tier == "BLOCKED"
    assert repeated_decision.notify is False
    assert repeated_decision.notification_key == previous_decision.notification_key


def test_runtime_parity_and_retired_updater_fail_closed() -> None:
    source = StaticSource()
    source.cells["ernie"] = replace(source.cells["ernie"], runtime_sha="0" * 40)
    state = collect_lifecycle_state(cells(), source)
    assert classify_exception(None, state).tier == "CRITICAL"

    source = StaticSource()
    source.retired_disabled = False
    state = collect_lifecycle_state(cells(), source)
    decision = classify_exception(None, state)
    assert (decision.tier, decision.required_decision) == ("CRITICAL", "disable_retired_in_place_updater")


def test_active_legacy_bert_automation_blocks_final_promotion_only() -> None:
    source = StaticSource()
    source.candidate = replace(source.candidate, stage="READY_FOR_APPROVAL")
    state = collect_lifecycle_state(cells(), source)
    decision = classify_exception(None, state)

    assert decision.tier == "BLOCKED"
    assert decision.required_decision == "pause_legacy_bert_health_automation_before_final_promotion"
    assert decision.prepare_candidate is False


def test_concurrent_tick_uses_one_nonblocking_lifecycle_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "monitor.lock"
    monitor = LifecycleMonitor(lock_path)
    with lock_path.open("a+b") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(LifecycleBlockedError) as error:
            monitor.tick(cells(), StaticSource())
    assert error.value.code == "lifecycle_monitor_locked"


def test_invalid_cell_or_release_evidence_fails_closed() -> None:
    source = StaticSource()
    source.cells["ernie"] = replace(source.cells["ernie"], rollback_artifact_sha256="")
    with pytest.raises(LifecycleBlockedError):
        collect_lifecycle_state(cells(), source)
