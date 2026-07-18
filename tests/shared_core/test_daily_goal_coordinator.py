from datetime import datetime
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


def test_failed_review_falls_through_to_fallback(tmp_path):
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
        if value.candidate_id == "first":
            return ExecutionOutcome(False, "bert", "", (), "review blocked")
        return ExecutionOutcome(True, "bert", "REVIEW_PASS", ("bert:review",))

    result = run_daily_cycle(
        mode="checkin",
        now=NOW,
        store=DailyGoalStore(tmp_path / "core.db"),
        collect_ernie=lambda: status("ernie", WorkStatus.NO_PENDING_WORK, [first]),
        collect_bert=lambda: status("bert", WorkStatus.NO_PENDING_WORK),
        execute=execute,
        review=review,
    )

    assert attempted == ["first", "daily-process-health-audit"]
    assert reviewed == ["first", "daily-process-health-audit"]
    assert (
        result.receipt.selected_goal == "Audit the Bert-Ernie daily coordination path"
    )


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
