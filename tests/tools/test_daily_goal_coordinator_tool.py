import json
from unittest.mock import patch

from tools.daily_goal_coordinator_tool import run_daily_goal_coordinator


def test_dry_run_never_posts_persists_executes_or_reviews(tmp_path):
    class ReadOnlyClient:
        def get(self, path):
            if path == "/v1/ernie/sessions":
                return {"sessions": []}
            if path == "/ik/ernie-dashboard/work-queue/list":
                return {"items": []}
            raise AssertionError(f"unexpected GET {path}")

        def post(self, path, payload):
            raise AssertionError("dry run must not POST")

    bert = json.dumps(
        {
            "success": True,
            "content": json.dumps(
                {
                    "status": "NO_PENDING_WORK",
                    "summary": "idle",
                    "evidence": ["tracker clear"],
                    "candidates": [],
                }
            ),
        }
    )
    with (
        patch(
            "tools.daily_goal_coordinator_tool.LoopbackJsonClient",
            return_value=ReadOnlyClient(),
        ),
        patch(
            "tools.daily_goal_coordinator_tool.call_orchestrator",
            return_value=bert,
        ) as orchestrator,
        patch("tools.daily_goal_coordinator_tool.execute_goal") as execute,
        patch("tools.daily_goal_coordinator_tool.review_goal") as review,
        patch.dict(
            "os.environ", {"SHARED_CORE_DB": str(tmp_path / "must-not-exist.db")}
        ),
    ):
        result = json.loads(run_daily_goal_coordinator(mode="checkin", dry_run=True))

    assert result["success"] is True
    assert result["dry_run"] is True
    assert result["suppress_delivery"] is True
    assert not (tmp_path / "must-not-exist.db").exists()
    execute.assert_not_called()
    review.assert_not_called()
    orchestrator.assert_not_called()


def _result_for_delivery(status):
    receipt = type(
        "Receipt",
        (),
        {
            "cycle_id": "daily-goal:2026-07-18",
            "telegram_delivery": status,
        },
    )()
    return type(
        "Result",
        (),
        {
            "receipt": receipt,
            "cycle_id": receipt.cycle_id,
            "message": "duplicate",
            "reran_work": False,
        },
    )()


def test_watchdog_is_silent_after_receipt_was_delivered():
    with patch(
        "tools.daily_goal_coordinator_tool.run_daily_cycle",
        return_value=_result_for_delivery("delivered"),
    ):
        payload = json.loads(run_daily_goal_coordinator(mode="watchdog", dry_run=True))

    assert payload["content"] == "[SILENT]"
    assert payload["suppress_delivery"] is True
    assert payload["reran_work"] is False


def test_watchdog_is_silent_after_ambiguous_delivery():
    with patch(
        "tools.daily_goal_coordinator_tool.run_daily_cycle",
        return_value=_result_for_delivery("unknown"),
    ):
        payload = json.loads(run_daily_goal_coordinator(mode="watchdog", dry_run=False))

    assert payload["content"] == "[SILENT]"
    assert payload["suppress_delivery"] is True


def test_watchdog_retries_failed_delivery_without_rerunning_work():
    with patch(
        "tools.daily_goal_coordinator_tool.run_daily_cycle",
        return_value=_result_for_delivery("failed"),
    ):
        payload = json.loads(run_daily_goal_coordinator(mode="watchdog", dry_run=False))

    assert payload["content"] == "duplicate"
    assert payload["suppress_delivery"] is False
    assert payload["reran_work"] is False


def test_reused_pending_receipt_is_silent_while_delivery_is_unreconciled():
    with patch(
        "tools.daily_goal_coordinator_tool.run_daily_cycle",
        return_value=_result_for_delivery("pending"),
    ):
        payload = json.loads(run_daily_goal_coordinator(mode="checkin", dry_run=False))

    assert payload["content"] == "[SILENT]"
    assert payload["suppress_delivery"] is True


def test_claim_loser_is_silent_without_receipt():
    result = type(
        "Result",
        (),
        {
            "receipt": None,
            "cycle_id": "daily-goal:2026-07-18",
            "message": "[SILENT]",
            "reran_work": False,
        },
    )()
    with patch(
        "tools.daily_goal_coordinator_tool.run_daily_cycle",
        return_value=result,
    ):
        payload = json.loads(run_daily_goal_coordinator(mode="checkin", dry_run=False))

    assert payload["content"] == "[SILENT]"
    assert payload["suppress_delivery"] is True


def test_invalid_mode_fails_closed():
    payload = json.loads(run_daily_goal_coordinator(mode="other", dry_run=True))
    assert payload["success"] is False
    assert "mode" in payload["error"]
