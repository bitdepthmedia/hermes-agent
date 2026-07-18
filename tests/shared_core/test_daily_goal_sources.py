import json
from datetime import datetime
from zoneinfo import ZoneInfo

from shared_core.daily_goal import WorkStatus
from shared_core.daily_goal_sources import (
    collect_bert_status,
    collect_ernie_status,
    queue_item_is_open,
)


NOW = datetime(2026, 7, 18, 9, 5, tzinfo=ZoneInfo("America/New_York"))


class FakeClient:
    def __init__(self, sessions, items):
        self.sessions = sessions
        self.items = items

    def get(self, path):
        if path == "/v1/ernie/sessions":
            return {"sessions": self.sessions}
        if path == "/ik/ernie-dashboard/work-queue/list":
            return {"items": self.items}
        raise AssertionError(path)


def test_completed_ready_item_is_not_open():
    item = {
        "status": "ready",
        "latest_outcome_decision": "completed",
        "latest_verification_decision": "passed",
        "latest_postcheck_decision": "passed",
    }
    assert queue_item_is_open(item) is False


def test_ernie_is_idle_only_when_sources_succeed_and_nothing_is_open():
    result = collect_ernie_status(FakeClient([], []), NOW)
    assert result.status is WorkStatus.NO_PENDING_WORK


def test_ernie_reports_pending_for_a_real_ready_item():
    result = collect_ernie_status(
        FakeClient([], [{"item_id": "work-1", "status": "ready"}]),
        NOW,
    )
    assert result.status is WorkStatus.PENDING_WORK


def test_ernie_reports_unknown_for_missing_or_malformed_queue_status():
    for item in ({"item_id": "missing"}, {"item_id": "malformed", "status": "unknown"}):
        result = collect_ernie_status(FakeClient([], [item]), NOW)
        assert result.status is WorkStatus.UNKNOWN


def test_ernie_reports_unknown_for_missing_or_malformed_session_status():
    for session in (
        {"session_id": "missing"},
        {"session_id": "malformed", "latest_status": "unrecognized-state"},
    ):
        result = collect_ernie_status(FakeClient([session], []), NOW)
        assert result.status is WorkStatus.UNKNOWN


def test_malformed_bert_payload_is_unknown():
    result = collect_bert_status(
        lambda **_: json.dumps({"success": True, "content": "not-json"}), NOW
    )
    assert result.status is WorkStatus.UNKNOWN
