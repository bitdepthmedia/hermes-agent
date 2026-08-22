from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from ik_lifecycle.approval_inbox import (
    ApprovalGrant,
    build_approval_item,
    validate_approval_grant,
    write_exception_once,
)
from ik_lifecycle.models import CellSpec, LifecycleBlockedError
from ik_lifecycle.monitor import classify_exception, collect_lifecycle_state
from tests.ik_lifecycle.test_monitor import StaticSource, cells


NOW = datetime(2026, 8, 22, 17, 0, tzinfo=timezone.utc)


def blocked_item():
    source = StaticSource()
    source.candidate = replace(source.candidate, stage="READY_FOR_APPROVAL")
    state = collect_lifecycle_state(cells(), source)
    return build_approval_item(classify_exception(None, state), state, created_at=NOW)


def test_clear_evidence_never_creates_an_approval_item() -> None:
    state = collect_lifecycle_state(cells(), StaticSource())
    assert build_approval_item(classify_exception(None, state), state, created_at=NOW) is None


def test_exception_item_is_redacted_exact_and_cannot_grant_authority() -> None:
    item = blocked_item()
    assert item is not None
    rendered = json.dumps(item.to_dict(), sort_keys=True)
    assert item.status == "DECISION_REQUIRED"
    assert item.grants_authority is False
    assert "/Users/" not in rendered
    assert "credential" not in rendered.lower()
    assert "secret" not in rendered.lower()


def test_write_is_idempotent_and_does_not_duplicate_notifications(tmp_path: Path) -> None:
    item = blocked_item()
    assert item is not None
    first = write_exception_once(tmp_path, item)
    second = write_exception_once(tmp_path, item)
    assert first == second
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert first.stat().st_mode & 0o077 == 0


def test_generic_stale_or_mismatched_approval_is_rejected() -> None:
    item = blocked_item()
    assert item is not None
    valid = ApprovalGrant(
        schema_id="ik.hermes.lifecycle-approval.v1",
        status="APPROVED",
        item_id=item.item_id,
        evidence_digest=item.evidence_digest,
        candidate_digest=item.candidate_digest,
        cell_id=item.cell_id,
        scope=item.required_decision,
        approved_at=NOW,
        expires_at=NOW + timedelta(minutes=30),
    )
    assert validate_approval_grant(item, valid, now=NOW).status == "VALIDATED_NOT_EXECUTED"

    for invalid in (
        replace(valid, scope="generic"),
        replace(valid, expires_at=NOW - timedelta(seconds=1)),
        replace(valid, candidate_digest="0" * 64),
    ):
        with pytest.raises(LifecycleBlockedError):
            validate_approval_grant(item, invalid, now=NOW)


def test_inbox_rejects_path_or_private_value_leakage(tmp_path: Path) -> None:
    item = blocked_item()
    assert item is not None
    leaked = replace(item, required_decision="inspect /Users/example/private")
    with pytest.raises(LifecycleBlockedError) as error:
        write_exception_once(tmp_path, leaked)
    assert error.value.code == "approval_item_privacy_invalid"
