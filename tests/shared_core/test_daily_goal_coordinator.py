import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from shared_core.daily_goal import (
    ActionKind,
    AgentStatus,
    DailyGoalStore,
    ImprovementCandidate,
    WorkStatus,
)
from shared_core.daily_goal_coordinator import run_daily_cycle
from shared_core.daily_goal_execution import ExecutionOutcome


NOW = datetime(2026, 7, 18, 9, 5, tzinfo=ZoneInfo("America/New_York"))


def status(agent, value, candidates=()):
    return AgentStatus(
        agent,
        value,
        value.value,
        (f"{agent}:verified",),
        NOW.isoformat(),
        tuple(candidates),
    )


def candidate(
    candidate_id,
    title,
    *,
    category="reliability",
    impact=5,
    owner="ernie",
    executor_id="system-health",
):
    return ImprovementCandidate(
        candidate_id,
        title,
        category,
        ("evidence",),
        impact,
        5,
        5,
        1,
        0,
        ActionKind.READ_ONLY_AUDIT,
        owner,
        executor_id,
    )


def test_pending_work_suppresses_improvement(tmp_path):
    calls = []
    result = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=DailyGoalStore(tmp_path / "core.db"),
        collect_ernie=lambda: status("ernie", WorkStatus.PENDING_WORK),
        collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK),
        execute=lambda *_: calls.append("execute"),
        review=lambda *_: calls.append("review"),
    )
    assert result.receipt.trigger == "normal_work"
    assert calls == []


def test_repeat_call_reuses_receipt_without_rerunning_work(tmp_path):
    store = DailyGoalStore(tmp_path / "core.db")
    calls = []

    def execute(*_):
        calls.append("execute")
        return ExecutionOutcome(True, "ernie", "audit complete", ("ernie:audit",))

    def review(*_):
        calls.append("review")
        return ExecutionOutcome(True, "bert", "REVIEW_PASS", ("bert:review",))

    kwargs = {
        "mode": "checkin",
        "now": NOW,
        "store": store,
        "collect_ernie": lambda: status("ernie", WorkStatus.NO_PENDING_WORK),
        "collect_bert": lambda: status("bert", WorkStatus.NO_PENDING_WORK),
        "execute": execute,
        "review": review,
    }
    first = run_daily_cycle(**kwargs)
    second = run_daily_cycle(**kwargs)
    assert first.receipt.cycle_id == second.receipt.cycle_id
    assert calls.count("execute") == 1
    assert calls.count("review") == 1
    assert second.reran_work is False


def test_blocked_top_candidate_falls_through_to_next_candidate(tmp_path):
    first = candidate("first", "First")
    second = candidate(
        "second",
        "Second",
        category="tests",
        impact=4,
        executor_id="gateway-dashboard",
    )
    attempted = []

    def execute(value, owner):
        attempted.append(value.candidate_id)
        if value.candidate_id == "first":
            return ExecutionOutcome(False, owner, "", (), "blocked")
        return ExecutionOutcome(True, owner, "passed", ("test:passed",))

    result = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=DailyGoalStore(tmp_path / "core.db"),
        collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK, [first]),
        collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK, [second]),
        execute=execute,
        review=lambda *_: ExecutionOutcome(
            True, "bert", "REVIEW_PASS", ("bert:review",)
        ),
    )
    assert attempted == ["first", "second"]
    assert result.receipt.selected_goal == "Second"
    assert "higher-ranked candidates were blocked" in result.receipt.selection_reason


def test_blocked_candidates_reach_deterministic_scheduler_health_fallback(tmp_path):
    attempted = []
    blocked = candidate("blocked", "Blocked")

    def execute(value, owner):
        attempted.append((value.candidate_id, value.executor_id))
        if value.candidate_id == "blocked":
            return ExecutionOutcome(False, owner, "", (), "blocked")
        return ExecutionOutcome(
            True, owner, "scheduler-health audit complete", ("ernie:audit",)
        )

    result = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=DailyGoalStore(tmp_path / "core.db"),
        collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK, [blocked]),
        collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK),
        execute=execute,
        review=lambda *_: ExecutionOutcome(
            True, "bert", "REVIEW_PASS", ("bert:review",)
        ),
    )

    assert attempted == [
        ("blocked", "system-health"),
        ("daily-process-health-audit", "scheduler-health"),
    ]
    assert (
        result.receipt.selected_goal == "Audit the Bert-Ernie daily coordination path"
    )


def test_failed_review_blocks_executed_goal_without_running_fallback(tmp_path):
    attempted = []
    reviewed = []
    first = candidate("first", "First")

    def execute(value, owner):
        attempted.append(value.candidate_id)
        return ExecutionOutcome(
            True, owner, f"{value.candidate_id} complete", ("audit",)
        )

    def review(value, *_):
        reviewed.append(value.candidate_id)
        return ExecutionOutcome(
            False, "bert", "", ("bert:review-failed",), "review blocked"
        )

    result = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=DailyGoalStore(tmp_path / "core.db"),
        collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK, [first]),
        collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK),
        execute=execute,
        review=review,
    )

    assert attempted == ["first"]
    assert reviewed == ["first"]
    assert result.receipt.selected_goal == "First"
    assert result.receipt.actions == ("first complete",)
    assert result.receipt.verification == ("audit", "bert:review-failed")
    assert result.receipt.blockers == ("first:review blocked",)


def test_reserved_fallback_identity_is_always_canonical(tmp_path):
    collision = ImprovementCandidate(
        "daily-process-health-audit",
        "Run arbitrary colliding work",
        "reliability",
        ("collision",),
        5,
        5,
        5,
        0,
        0,
        ActionKind.PATCH_PROPOSAL,
        "bert",
        "patch-proposal",
    )
    seen = []

    def execute(value, owner):
        seen.append(value)
        return ExecutionOutcome(
            True, owner, "scheduler-health audit complete", ("canonical",)
        )

    result = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=DailyGoalStore(tmp_path / "core.db"),
        collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK, [collision]),
        collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK),
        execute=execute,
        review=lambda *_: ExecutionOutcome(
            True, "bert", "REVIEW_PASS", ("bert:review",)
        ),
    )

    assert len(seen) == 1
    assert seen[0].title == "Audit the Bert-Ernie daily coordination path"
    assert seen[0].recommended_owner == "ernie"
    assert seen[0].action_kind is ActionKind.READ_ONLY_AUDIT
    assert seen[0].executor_id == "scheduler-health"
    assert result.receipt.ranked_candidates == ()


def test_watchdog_retries_one_unknown_checkin(tmp_path):
    store = DailyGoalStore(tmp_path / "core.db")
    calls = {"bert": 0}

    def bert():
        calls["bert"] += 1
        value = WorkStatus.UNKNOWN if calls["bert"] == 1 else WorkStatus.PENDING_WORK
        return status("bert", value)

    first = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=store,
        collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK),
        collect_bert=bert,
        execute=lambda *_: None,
        review=lambda *_: None,
    )
    second = run_daily_cycle(
        mode="watchdog",
        now=NOW,
        store=store,
        collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK),
        collect_bert=bert,
        execute=lambda *_: None,
        review=lambda *_: None,
    )
    assert first.receipt.trigger == "unknown"
    assert second.receipt.trigger == "normal_work"
    assert calls["bert"] == 2


def test_watchdog_retries_unknown_only_once(tmp_path):
    store = DailyGoalStore(tmp_path / "core.db")
    calls = {"bert": 0}

    def bert():
        calls["bert"] += 1
        return status("bert", WorkStatus.UNKNOWN)

    kwargs = {
        "now": NOW,
        "store": store,
        "collect_ernie": lambda: status("ernie", WorkStatus.NO_PENDING_WORK),
        "collect_bert": bert,
        "execute": lambda *_: None,
        "review": lambda *_: None,
    }
    run_daily_cycle(mode="checkin", **kwargs)
    retried = run_daily_cycle(mode="watchdog", **kwargs)
    reused = run_daily_cycle(mode="watchdog", **kwargs)

    assert retried.reran_work is True
    assert reused.reran_work is False
    assert calls["bert"] == 2


def test_watchdog_never_reruns_completed_work(tmp_path):
    store = DailyGoalStore(tmp_path / "core.db")
    calls = []

    result = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=store,
        collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK),
        collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK),
        execute=lambda value, owner: (
            calls.append(value.candidate_id)
            or ExecutionOutcome(True, owner, "audit complete", ("audit",))
        ),
        review=lambda *_: ExecutionOutcome(
            True, "bert", "REVIEW_PASS", ("bert:review",)
        ),
    )
    store.update_delivery(result.receipt.cycle_id, "delivered")
    watchdog = run_daily_cycle(
        mode="watchdog",
        now=NOW,
        store=store,
        collect_ernie=lambda: (_ for _ in ()).throw(
            AssertionError("must not recollect")
        ),
        collect_bert=lambda: (_ for _ in ()).throw(
            AssertionError("must not recollect")
        ),
        execute=lambda *_: (_ for _ in ()).throw(AssertionError("must not execute")),
        review=lambda *_: (_ for _ in ()).throw(AssertionError("must not review")),
    )

    assert calls == ["daily-process-health-audit"]
    assert watchdog.reran_work is False
    assert watchdog.receipt.telegram_delivery == "delivered"


def test_uses_america_new_york_date_for_cycle_identity(tmp_path):
    utc_now = datetime(2026, 7, 19, 2, 0, tzinfo=ZoneInfo("UTC"))
    result = run_daily_cycle(
        mode="checkin",
        now=utc_now,
        store=DailyGoalStore(tmp_path / "core.db"),
        collect_ernie=lambda: status("ernie", WorkStatus.PENDING_WORK),
        collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK),
        execute=lambda *_: None,
        review=lambda *_: None,
    )

    assert result.receipt.cycle_id == "daily-goal:2026-07-18"


def test_concurrent_checkins_execute_and_review_exactly_once(tmp_path):
    database = tmp_path / "core.db"
    barrier = threading.Barrier(2)
    counter_lock = threading.Lock()
    calls = {"execute": 0, "review": 0}

    class RacingStore(DailyGoalStore):
        def __init__(self, path):
            super().__init__(path)
            self._waited = False

        def get_receipt(self, cycle_id):
            receipt = super().get_receipt(cycle_id)
            if receipt is None and not self._waited:
                self._waited = True
                barrier.wait(timeout=5)
            return receipt

    def invoke():
        def execute(value, owner):
            with counter_lock:
                calls["execute"] += 1
            time.sleep(0.05)
            return ExecutionOutcome(True, owner, "audit complete", ("audit",))

        def review(*_):
            with counter_lock:
                calls["review"] += 1
            return ExecutionOutcome(True, "bert", "REVIEW_PASS", ("bert:review",))

        return run_daily_cycle(
            mode="checkin",
            now=NOW,
            store=RacingStore(database),
            collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK),
            collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK),
            execute=execute,
            review=review,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoke(), range(2)))

    assert calls == {"execute": 1, "review": 1}
    assert sum(result.receipt is not None for result in results) == 1
    loser = next(result for result in results if result.receipt is None)
    assert loser.message == "[SILENT]"
    assert loser.reran_work is False
    assert (
        DailyGoalStore(database).get_receipt("daily-goal:2026-07-18").selected_goal
        == "Audit the Bert-Ernie daily coordination path"
    )


def test_concurrent_watchdogs_retry_unknown_exactly_once(tmp_path):
    database = tmp_path / "core.db"
    initial = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=DailyGoalStore(database),
        collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK),
        collect_bert=lambda: status("bert", WorkStatus.UNKNOWN),
        execute=lambda *_: None,
        review=lambda *_: None,
    )
    assert initial.receipt.trigger == "unknown"

    barrier = threading.Barrier(2)
    counter_lock = threading.Lock()
    calls = {"collect": 0}

    class RacingStore(DailyGoalStore):
        def __init__(self, path):
            super().__init__(path)
            self._waited = False

        def get_receipt(self, cycle_id):
            receipt = super().get_receipt(cycle_id)
            if receipt is not None and not self._waited:
                self._waited = True
                barrier.wait(timeout=5)
            return receipt

    def collect_bert():
        with counter_lock:
            calls["collect"] += 1
        return status("bert", WorkStatus.PENDING_WORK)

    def invoke():
        return run_daily_cycle(
            mode="watchdog",
            now=NOW,
            store=RacingStore(database),
            collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK),
            collect_bert=collect_bert,
            execute=lambda *_: None,
            review=lambda *_: None,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: invoke(), range(2)))

    assert calls["collect"] == 1
    assert sum(result.receipt is not None for result in results) == 1
    assert (
        DailyGoalStore(database).get_receipt("daily-goal:2026-07-18").trigger
        == "normal_work"
    )


def test_delivered_unknown_retry_with_new_content_starts_pending(tmp_path):
    store = DailyGoalStore(tmp_path / "core.db")
    morning = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=store,
        collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK),
        collect_bert=lambda: status("bert", WorkStatus.UNKNOWN),
        execute=lambda *_: None,
        review=lambda *_: None,
    )
    store.update_delivery(morning.receipt.cycle_id, "delivered")

    watchdog = run_daily_cycle(
        mode="watchdog",
        now=NOW,
        store=store,
        collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK),
        collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK),
        execute=lambda _candidate, owner: ExecutionOutcome(
            True, owner, "audit complete", ("audit",)
        ),
        review=lambda *_: ExecutionOutcome(
            True, "bert", "REVIEW_PASS", ("bert:review",)
        ),
    )

    assert watchdog.receipt.trigger == "improvement"
    assert watchdog.receipt.telegram_delivery == "pending"


def test_fresh_claim_loser_is_silent(tmp_path):
    store = DailyGoalStore(tmp_path / "core.db")
    cycle = store.get_or_create_cycle(NOW.date())
    assert store.try_claim(cycle.cycle_id, "checkin", now=NOW) is True

    result = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=store,
        collect_ernie=lambda: (_ for _ in ()).throw(
            AssertionError("fresh loser must not collect")
        ),
        collect_bert=lambda: (_ for _ in ()).throw(
            AssertionError("fresh loser must not collect")
        ),
        execute=lambda *_: None,
        review=lambda *_: None,
    )

    assert result.receipt is None
    assert result.message == "[SILENT]"


def test_stale_claim_takeover_runs_once(tmp_path):
    store = DailyGoalStore(tmp_path / "core.db")
    cycle = store.get_or_create_cycle(NOW.date())
    assert (
        store.try_claim(
            cycle.cycle_id,
            "checkin",
            now=NOW - timedelta(hours=1),
        )
        is True
    )
    calls = {"collect": 0}

    def collect_ernie():
        calls["collect"] += 1
        return status("ernie", WorkStatus.PENDING_WORK)

    first = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=store,
        collect_ernie=collect_ernie,
        collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK),
        execute=lambda *_: None,
        review=lambda *_: None,
    )
    second = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=store,
        collect_ernie=lambda: (_ for _ in ()).throw(
            AssertionError("completed takeover must not recollect")
        ),
        collect_bert=lambda: (_ for _ in ()).throw(
            AssertionError("completed takeover must not recollect")
        ),
        execute=lambda *_: None,
        review=lambda *_: None,
    )

    assert first.receipt.trigger == "normal_work"
    assert second.reran_work is False
    assert calls["collect"] == 1


def test_collector_exception_persists_unknown_receipt(tmp_path):
    result = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=DailyGoalStore(tmp_path / "core.db"),
        collect_ernie=lambda: (_ for _ in ()).throw(RuntimeError("collector crashed")),
        collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK),
        execute=lambda *_: None,
        review=lambda *_: None,
    )

    assert result.receipt.trigger == "unknown"
    assert result.receipt.ernie_status == "UNKNOWN"
    assert any("RuntimeError" in blocker for blocker in result.receipt.blockers)
