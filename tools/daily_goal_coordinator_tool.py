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
from tools.registry import registry, tool_error


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
        operator_reconciliation = delivery_status in {"attempting", "unknown"}
        suppress_delivery = (
            bool(dry_run)
            or receipt is None
            or delivery_status in {"attempting", "delivered", "unknown"}
        )
        content = (
            "[SILENT]"
            if receipt is None
            or delivery_status in {"attempting", "delivered", "unknown"}
            else result.message
        )
        return json.dumps(
            {
                "success": True,
                "content": content,
                "cycle_id": result.cycle_id,
                "reran_work": result.reran_work,
                "dry_run": bool(dry_run),
                "suppress_delivery": suppress_delivery,
                "operator_reconciliation": operator_reconciliation,
            }
        )
    except Exception as exc:
        return tool_error(
            f"daily goal coordinator failed: {type(exc).__name__}: {exc}",
            success=False,
        )


def begin_daily_goal_delivery(cycle_id: str) -> bool:
    database = Path(
        os.getenv(
            "SHARED_CORE_DB",
            get_hermes_home() / "daily-goal" / "daily-goal.db",
        )
    )
    return DailyGoalStore(database).begin_delivery(cycle_id) is not None


def record_daily_goal_delivery(cycle_id: str, status: str) -> None:
    if status not in {"delivered", "failed", "unknown"}:
        raise ValueError("delivery status must be delivered, failed, or unknown")
    database = Path(
        os.getenv(
            "SHARED_CORE_DB",
            get_hermes_home() / "daily-goal" / "daily-goal.db",
        )
    )
    DailyGoalStore(database).update_delivery(cycle_id, status)


registry.register(
    name="daily_goal_coordinator",
    toolset="orchestrator",
    schema={
        "name": "daily_goal_coordinator",
        "description": (
            "Run the strict Bert-Ernie daily tracker and fallback "
            "improvement coordinator."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["checkin", "watchdog"],
                },
                "dry_run": {"type": "boolean"},
            },
        },
    },
    handler=lambda args, **_: run_daily_goal_coordinator(
        mode=str(args.get("mode") or "checkin"),
        dry_run=bool(args.get("dry_run", False)),
    ),
)


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
