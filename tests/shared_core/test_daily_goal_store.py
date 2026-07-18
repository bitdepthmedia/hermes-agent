import hashlib
import json
from datetime import UTC, date, datetime, timedelta

import pytest

from shared_core.daily_goal import (
    AgentStatus,
    CLAIM_LEASE_SECONDS,
    CycleState,
    DailyGoalStore,
    DailyReceipt,
    WorkStatus,
    resolve_trigger,
)


def status(agent: str, value: WorkStatus) -> AgentStatus:
    return AgentStatus(
        agent=agent,
        status=value,
        summary=value.value,
        evidence=(f"{agent}:{value.value}",),
        freshness_at="2026-07-18T09:05:00-04:00",
        candidates=(),
        history_complete=True,
        source_receipts=(f"{agent}:source",),
    )


def test_trigger_requires_two_explicit_idle_statuses():
    assert (
        resolve_trigger(
            status("ernie", WorkStatus.NO_PENDING_WORK),
            status("bert", WorkStatus.NO_PENDING_WORK),
        )
        is CycleState.IMPROVEMENT_SELECTING
    )
    assert (
        resolve_trigger(
            status("ernie", WorkStatus.PENDING_WORK),
            status("bert", WorkStatus.NO_PENDING_WORK),
        )
        is CycleState.NORMAL_WORK
    )
    assert (
        resolve_trigger(
            status("ernie", WorkStatus.NO_PENDING_WORK),
            status("bert", WorkStatus.UNKNOWN),
        )
        is CycleState.UNKNOWN
    )


def test_pending_work_has_precedence_over_unknown():
    assert (
        resolve_trigger(
            status("ernie", WorkStatus.PENDING_WORK),
            status("bert", WorkStatus.UNKNOWN),
        )
        is CycleState.NORMAL_WORK
    )
    assert (
        resolve_trigger(
            status("ernie", WorkStatus.UNKNOWN),
            status("bert", WorkStatus.PENDING_WORK),
        )
        is CycleState.NORMAL_WORK
    )


def test_get_or_create_cycle_is_date_idempotent(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")

    first = store.get_or_create_cycle(date(2026, 7, 18))
    second = store.get_or_create_cycle(date(2026, 7, 18))

    assert first.cycle_id == second.cycle_id == "daily-goal:2026-07-18"
    assert store.list_cycles() == [first]


def test_delivery_status_can_be_reconciled_by_watchdog(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    receipt = store.save_receipt(
        DailyReceipt(
            cycle.cycle_id,
            "PENDING_WORK",
            "NO_PENDING_WORK",
            "normal_work",
            (),
            None,
            None,
            None,
            None,
            (),
            (),
            (),
            "pending",
        )
    )
    assert receipt.telegram_delivery == "pending"
    assert (
        store.update_delivery(cycle.cycle_id, "delivered").telegram_delivery
        == "delivered"
    )


def test_ambiguous_delivery_enqueues_one_operator_alert(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    store.save_receipt(
        DailyReceipt(
            cycle.cycle_id, "PENDING_WORK", "NO_PENDING_WORK", "normal_work",
            (), None, None, None, None, (), (), (), "pending",
        )
    )
    assert store.begin_delivery(cycle.cycle_id) is not None
    store.update_delivery(cycle.cycle_id, "unknown", error="send timed out")
    store.update_delivery(cycle.cycle_id, "unknown", error="duplicate callback")
    alert = store.get_alert(cycle.cycle_id)
    assert alert["state"] == "pending"
    assert "send timed out" in alert["last_error"]
    assert store.begin_delivery(cycle.cycle_id) is None
    assert store.begin_alert_delivery(cycle.cycle_id) is True
    assert store.begin_alert_delivery(cycle.cycle_id) is False
    store.update_alert_delivery(cycle.cycle_id, "delivered")
    assert store.get_alert(cycle.cycle_id)["state"] == "delivered"


def test_prior_cycle_exhausted_alert_is_drainable_later(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    old = store.get_or_create_cycle(date(2026, 7, 17))
    store.save_receipt(DailyReceipt(
        old.cycle_id, "PENDING_WORK", "NO_PENDING_WORK", "normal_work",
        (), None, None, None, None, (), (), (), "pending",
    ))
    for _ in range(2):
        assert store.begin_delivery(old.cycle_id) is not None
        store.update_delivery(old.cycle_id, "failed", error="definitive")
    store.get_or_create_cycle(date(2026, 7, 18))
    alert = store.get_next_alert()
    assert alert["cycle_id"] == old.cycle_id
    assert store.begin_alert_delivery(old.cycle_id) is True
    store.update_alert_delivery(old.cycle_id, "delivered")
    assert store.get_next_alert() is None


def test_tampered_structured_review_receipt_is_rejected(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    statement = "The bounded fixed endpoint evidence supports the audit result."
    source = "bert-no-tools:source-receipt"
    metrics_hash = hashlib.sha256(b"audit complete").hexdigest()
    review_hash = hashlib.sha256(
        json.dumps(
            {
                "metrics_hash": metrics_hash,
                "source": source,
                "statement": statement,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    receipt = DailyReceipt(
        cycle.cycle_id,
        "NO_PENDING_WORK",
        "NO_PENDING_WORK",
        "improvement",
        (),
        "Audit",
        "selected",
        "ernie",
        "bert",
        ("audit complete",),
        ("verified",),
        (),
        "pending",
        outcome="completed",
        review_statement=statement,
        review_hash=review_hash,
        review_source=source,
        review_metrics_hash=metrics_hash,
        review_observations=(("metric", "audit"),),
    )
    store.save_receipt(receipt)
    raw = store._conn.execute(
        "SELECT receipt FROM daily_goal_receipts WHERE cycle_id = ?",
        (cycle.cycle_id,),
    ).fetchone()["receipt"]
    data = json.loads(raw)
    data["review_statement"] = "Tampered counterpart statement"
    store._conn.execute(
        "UPDATE daily_goal_receipts SET receipt = ? WHERE cycle_id = ?",
        (json.dumps(data), cycle.cycle_id),
    )
    store._conn.commit()

    with pytest.raises(ValueError, match="integrity hash mismatch"):
        store.get_receipt(cycle.cycle_id)


def test_tampered_review_source_is_rejected(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    statement = "The bounded fixed endpoint evidence supports the audit result."
    source = "bert-no-tools:source-receipt"
    metrics_hash = hashlib.sha256(b"audit complete").hexdigest()
    review_hash = hashlib.sha256(
        json.dumps(
            {
                "metrics_hash": metrics_hash,
                "source": source,
                "statement": statement,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    store.save_receipt(
        DailyReceipt(
            cycle.cycle_id,
            "NO_PENDING_WORK",
            "NO_PENDING_WORK",
            "improvement",
            (),
            "Audit",
            "selected",
            "ernie",
            "bert",
            ("audit complete",),
            ("verified",),
            (),
            "pending",
            outcome="completed",
            review_statement=statement,
            review_hash=review_hash,
            review_source=source,
            review_metrics_hash=metrics_hash,
            review_observations=(("metric", "audit"),),
        )
    )
    raw = store._conn.execute(
        "SELECT receipt FROM daily_goal_receipts WHERE cycle_id = ?",
        (cycle.cycle_id,),
    ).fetchone()["receipt"]
    data = json.loads(raw)
    data["review_source"] = "bert-no-tools:tampered"
    store._conn.execute(
        "UPDATE daily_goal_receipts SET receipt = ? WHERE cycle_id = ?",
        (json.dumps(data), cycle.cycle_id),
    )
    store._conn.commit()

    with pytest.raises(ValueError, match="integrity hash mismatch"):
        store.get_receipt(cycle.cycle_id)


def test_completed_review_fields_cannot_be_deleted(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    receipt = DailyReceipt(
        cycle.cycle_id, "NO_PENDING_WORK", "NO_PENDING_WORK", "improvement",
        (), "Audit", "selected", "ernie", "bert", ("audit complete",),
        ("verified",), (), "pending", outcome="completed",
        review_statement="Structured observations support this fixed audit.",
        review_hash="a" * 64,
        review_source="bert-no-tools:receipt",
        review_metrics_hash="b" * 64,
    )
    # Persist a syntactically complete receipt, then simulate field deletion.
    data = json.loads(json.dumps(receipt.__dict__))
    data["decision_integrity_hash"] = ""
    data["review_statement"] = None
    store._conn.execute(
        "INSERT INTO daily_goal_receipts VALUES (?, ?, ?)",
        (cycle.cycle_id, json.dumps(data), datetime.now(UTC).isoformat()),
    )
    store._conn.commit()
    with pytest.raises(ValueError, match="lacks decision integrity"):
        store.get_receipt(cycle.cycle_id)


def test_claim_is_atomic_across_store_connections(tmp_path):
    database = tmp_path / "shared-core.db"
    first = DailyGoalStore(database)
    second = DailyGoalStore(database)
    cycle = first.get_or_create_cycle(date(2026, 7, 18))
    now = datetime(2026, 7, 18, 13, 0, tzinfo=UTC)

    assert first.try_claim(cycle.cycle_id, "checkin", now=now) is True
    assert second.try_claim(cycle.cycle_id, "checkin", now=now) is False


def test_stale_claim_can_be_taken_over_exactly_once(tmp_path):
    database = tmp_path / "shared-core.db"
    first = DailyGoalStore(database)
    second = DailyGoalStore(database)
    third = DailyGoalStore(database)
    cycle = first.get_or_create_cycle(date(2026, 7, 18))
    claimed_at = datetime(2026, 7, 18, 13, 0, tzinfo=UTC)
    takeover_at = claimed_at + timedelta(seconds=CLAIM_LEASE_SECONDS + 1)

    assert first.try_claim(cycle.cycle_id, "checkin", now=claimed_at) is True
    assert second.try_claim(cycle.cycle_id, "checkin", now=takeover_at) is True
    assert third.try_claim(cycle.cycle_id, "checkin", now=takeover_at) is False


def test_unknown_retry_is_durable_after_lease_window(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    first = datetime(2026, 7, 18, 13, 0, tzinfo=UTC)
    much_later = first + timedelta(hours=4)
    assert store.try_claim(cycle.cycle_id, "unknown_retry", now=first) is True
    assert store.try_claim(cycle.cycle_id, "unknown_retry", now=much_later) is True
    store.complete_unknown_retry(cycle.cycle_id)
    assert store.try_claim(
        cycle.cycle_id, "unknown_retry", now=much_later + timedelta(hours=4)
    ) is False


def test_delivery_error_is_redacted_before_alert_persistence(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    store.save_receipt(DailyReceipt(
        cycle.cycle_id, "PENDING_WORK", "NO_PENDING_WORK", "normal_work",
        (), None, None, None, None, (), (), (), "pending",
    ))
    store.begin_delivery(cycle.cycle_id)
    secret = "https://api.telegram.org/bot123456:ABC/send?token=hunter2 Authorization: Bearer xyz"
    store.update_delivery(cycle.cycle_id, "unknown", error=secret)
    rendered = store.get_alert(cycle.cycle_id)["last_error"]
    assert "123456:ABC" not in rendered
    assert "hunter2" not in rendered
    assert "Bearer xyz" not in rendered


def test_new_semantic_receipt_resets_delivery_state(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    original = DailyReceipt(
        cycle.cycle_id,
        "UNKNOWN",
        "NO_PENDING_WORK",
        "unknown",
        (),
        None,
        None,
        None,
        None,
        (),
        (),
        ("unknown",),
        "pending",
    )
    store.save_receipt(original)
    store.update_delivery(cycle.cycle_id, "delivered")

    retried = DailyReceipt(
        cycle.cycle_id,
        "NO_PENDING_WORK",
        "PENDING_WORK",
        "normal_work",
        (),
        None,
        None,
        None,
        None,
        (),
        ("bert:verified",),
        (),
        "pending",
    )
    saved = store.save_receipt(retried)

    assert saved.trigger == "normal_work"
    assert saved.telegram_delivery == "pending"


def test_same_semantic_receipt_preserves_reconciled_delivery_state(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    receipt = DailyReceipt(
        cycle.cycle_id,
        "PENDING_WORK",
        "NO_PENDING_WORK",
        "normal_work",
        (),
        None,
        None,
        None,
        None,
        (),
        ("bert:verified",),
        (),
        "pending",
    )
    store.save_receipt(receipt)
    store.update_delivery(cycle.cycle_id, "delivered")

    assert store.save_receipt(receipt).telegram_delivery == "delivered"


def test_delivery_reconciliation_does_not_downgrade_terminal_state(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    store.save_receipt(
        DailyReceipt(
            cycle.cycle_id,
            "PENDING_WORK",
            "NO_PENDING_WORK",
            "normal_work",
            (),
            None,
            None,
            None,
            None,
            (),
            (),
            (),
            "pending",
        )
    )
    store.update_delivery(cycle.cycle_id, "delivered")

    assert (
        store.update_delivery(cycle.cycle_id, "failed").telegram_delivery == "delivered"
    )


def test_delivery_outbox_tracks_attempts_and_stops_after_ambiguous_result(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    store.save_receipt(
        DailyReceipt(
            cycle.cycle_id,
            "PENDING_WORK",
            "NO_PENDING_WORK",
            "normal_work",
            (),
            None,
            None,
            None,
            None,
            (),
            (),
            (),
            "pending",
        )
    )

    first = store.begin_delivery(cycle.cycle_id)
    assert first is not None
    assert first.telegram_delivery == "attempting"
    assert first.delivery_attempts == 1

    failed = store.update_delivery(
        cycle.cycle_id,
        "failed",
        error="definitive rejection",
    )
    assert failed.telegram_delivery == "failed"
    assert failed.delivery_attempts == 1
    assert failed.delivery_last_error == "definitive rejection"

    second = store.begin_delivery(cycle.cycle_id)
    assert second is not None
    assert second.telegram_delivery == "attempting"
    assert second.delivery_attempts == 2

    ambiguous = store.update_delivery(
        cycle.cycle_id,
        "unknown",
        error="adapter timeout",
    )
    assert ambiguous.telegram_delivery == "unknown"
    assert ambiguous.delivery_attempts == 2
    assert ambiguous.delivery_last_error == "adapter timeout"
    assert store.begin_delivery(cycle.cycle_id) is None


def test_semantic_receipt_replacement_resets_delivery_outbox(tmp_path):
    store = DailyGoalStore(tmp_path / "shared-core.db")
    cycle = store.get_or_create_cycle(date(2026, 7, 18))
    original = DailyReceipt(
        cycle.cycle_id,
        "UNKNOWN",
        "UNKNOWN",
        "unknown",
        (),
        None,
        None,
        None,
        None,
        (),
        (),
        ("status unavailable",),
        "pending",
    )
    store.save_receipt(original)
    assert store.begin_delivery(cycle.cycle_id).delivery_attempts == 1
    store.update_delivery(cycle.cycle_id, "delivered")

    replacement = DailyReceipt(
        cycle.cycle_id,
        "PENDING_WORK",
        "UNKNOWN",
        "normal_work",
        (),
        None,
        None,
        None,
        None,
        (),
        ("ernie:pending",),
        (),
        "pending",
    )
    saved = store.save_receipt(replacement)

    assert saved.telegram_delivery == "pending"
    assert saved.delivery_attempts == 0
    assert saved.delivery_last_attempt_at is None
    assert saved.delivery_last_error is None
