from datetime import UTC, date, datetime, timedelta

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
