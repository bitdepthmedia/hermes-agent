"""Direct cron tool for the strict daily Bert-Ernie coordinator."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hermes_constants import get_hermes_home
from shared_core.daily_goal import (
    AgentStatus,
    DailyGoalStore,
    WorkStatus,
)
from shared_core.daily_goal_coordinator import run_daily_cycle
from shared_core.daily_goal_execution import (
    execute_goal,
    review_goal,
)
from shared_core.daily_goal_sources import (
    LoopbackJsonClient,
    collect_bert_status,
    collect_ernie_status,
)
from tools.call_orchestrator_read_only import call_orchestrator_read_only
from tools.registry import tool_error


NEW_YORK = ZoneInfo("America/New_York")


def _dry_run_bert_status(now: datetime) -> AgentStatus:
    return AgentStatus(
        "bert",
        WorkStatus.UNKNOWN,
        "Dry run skipped POST-based Bert status collection",
        ("dry-run:no-bert-post",),
        now.isoformat(),
        (),
    )


def run_daily_goal_coordinator(mode: str = "checkin", dry_run: bool = False) -> str:
    if mode not in {"checkin", "watchdog"}:
        return tool_error("mode must be checkin or watchdog", success=False)

    try:
        now = datetime.now(NEW_YORK)
        database = Path(
            os.getenv(
                "SHARED_CORE_DB",
                get_hermes_home() / "daily-goal" / "daily-goal.db",
            )
        )
        if not dry_run:
            database.parent.mkdir(parents=True, exist_ok=True)

        ernie = LoopbackJsonClient("http://127.0.0.1:8642")
        store = DailyGoalStore(":memory:" if dry_run else database)
        if dry_run:
            collect_bert = lambda: _dry_run_bert_status(now)

            def execute(*_):
                raise AssertionError("dry run must not execute a goal")

            def review(*_):
                raise AssertionError("dry run must not review a goal")

        else:
            collect_bert = lambda: collect_bert_status(
                call_orchestrator_read_only,
                now,
            )
            execute = lambda candidate, owner: execute_goal(
                candidate,
                owner=owner,
                ernie=ernie,
                call_orchestrator=call_orchestrator_read_only,
            )
            review = lambda candidate, owner, summary: review_goal(
                candidate,
                owner=owner,
                execution_summary=summary,
                ernie=ernie,
                call_orchestrator=call_orchestrator_read_only,
            )

        result = run_daily_cycle(
            mode=mode,
            now=now,
            store=store,
            collect_ernie=lambda: collect_ernie_status(ernie, now),
            collect_bert=collect_bert,
            execute=execute,
            review=review,
        )
        receipt = result.receipt
        delivery_status = receipt.telegram_delivery if receipt is not None else None
        alert = (
            store.get_next_alert()
            if receipt is not None and not dry_run
            else None
        )
        alert_eligible = bool(
            alert is not None
            and alert["state"] in {"pending", "failed"}
            and alert["attempt_count"] < 2
        )
        delivery_cycle_id = (
            str(alert["cycle_id"]) if alert_eligible else result.cycle_id
        )
        operator_reconciliation = delivery_status in {"attempting", "unknown"} or bool(alert)
        suppress_delivery = (
            bool(dry_run)
            or receipt is None
            or (
                delivery_status in {"attempting", "delivered", "unknown"}
                and not alert_eligible
            )
        )
        content = (
            (
                "OPERATOR ALERT: Daily goal delivery requires reconciliation. "
                + str(alert["last_error"])[:500]
            )
            if alert_eligible
            else (
                "[SILENT]"
                if receipt is None
                or delivery_status in {"attempting", "delivered", "unknown"}
                else result.message
            )
        )
        return json.dumps(
            {
                "success": True,
                "content": content,
                "cycle_id": delivery_cycle_id,
                "reran_work": result.reran_work,
                "dry_run": bool(dry_run),
                "suppress_delivery": suppress_delivery,
                "operator_reconciliation": operator_reconciliation,
                "delivery_kind": "operator_alert" if alert_eligible else "original",
            }
        )
    except Exception as exc:
        return tool_error(
            f"daily goal coordinator failed: {type(exc).__name__}: {exc}",
            success=False,
        )


def begin_daily_goal_delivery(cycle_id: str, delivery_kind: str = "original") -> bool:
    database = Path(
        os.getenv(
            "SHARED_CORE_DB",
            get_hermes_home() / "daily-goal" / "daily-goal.db",
        )
    )
    store = DailyGoalStore(database)
    if delivery_kind == "operator_alert":
        return store.begin_alert_delivery(cycle_id)
    return store.begin_delivery(cycle_id) is not None


def record_daily_goal_delivery(
    cycle_id: str,
    status: str,
    delivery_kind: str = "original",
    error: str | None = None,
) -> None:
    if status not in {"delivered", "failed", "unknown"}:
        raise ValueError("delivery status must be delivered, failed, or unknown")
    database = Path(
        os.getenv(
            "SHARED_CORE_DB",
            get_hermes_home() / "daily-goal" / "daily-goal.db",
        )
    )
    store = DailyGoalStore(database)
    if delivery_kind == "operator_alert":
        store.update_alert_delivery(cycle_id, status, error=error)
    else:
        store.update_delivery(cycle_id, status, error=error)


def _main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--cron-json", required=True)
    options = parser.parse_args()
    try:
        arguments = json.loads(options.cron_json)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid cron arguments: {exc}") from exc
    if not isinstance(arguments, dict):
        raise SystemExit("cron arguments must be a JSON object")
    print(
        run_daily_goal_coordinator(
            mode=arguments.get("mode", "checkin"),
            dry_run=arguments.get("dry_run", False),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
