import hashlib
import json
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from shared_core.daily_goal import WorkStatus
from shared_core.daily_goal_sources import (
    LoopbackJsonClient,
    collect_bert_status,
    collect_ernie_status,
    queue_item_is_open,
)


NOW = datetime(2026, 7, 18, 9, 5, tzinfo=ZoneInfo("America/New_York"))


def _canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


class FakeReadOnlyStatus:
    def __init__(self, content, *, complete=True, tamper=False):
        self.content = content
        self.complete = complete
        self.tamper = tamper

    def __call__(self, input_text, **kwargs):
        items = [
            {
                "kind": "session_db_metadata",
                "available": True,
                "pagination": {
                    "complete": self.complete,
                    "truncated": not self.complete,
                },
                "records": [
                    {
                        "id": "history-1",
                        "ended_at": "2026-07-17T10:00:00+00:00",
                    }
                ],
            },
            {
                "kind": "cron_metadata",
                "available": True,
                "pagination": {"complete": True, "truncated": False},
                "records": [],
            },
        ]
        receipts = {
            "purpose": "status",
            "coverage": {"complete": self.complete},
            "derived_status": {
                "status": "NO_PENDING_WORK" if self.complete else "UNKNOWN",
                "evidence_refs": ["coverage:complete"] if self.complete else [],
            },
            "items": items,
        }
        payload = {
            "purpose": "status",
            "input": input_text,
            "max_tokens": kwargs["max_tokens"],
        }
        attestation = {
            "mode": "no_tools",
            "enabled_toolsets": [],
            "tool_names": [],
            "tool_calls": 0,
            "request_sha256": hashlib.sha256(_canonical(payload)).hexdigest(),
            "input_sha256": hashlib.sha256(input_text.encode()).hexdigest(),
            "output_sha256": hashlib.sha256(self.content.encode()).hexdigest(),
            "source_receipts_sha256": hashlib.sha256(
                _canonical(receipts)
            ).hexdigest(),
        }
        if self.tamper:
            attestation["tool_calls"] = 1
        return json.dumps(
            {
                "success": True,
                "content": self.content,
                "source_receipts": receipts,
                "attestation": attestation,
            }
        )


class FakeClient:
    def __init__(
        self,
        sessions,
        items,
        *,
        session_count=None,
        item_count=None,
        status_counts=None,
    ):
        self.sessions = sessions
        self.items = items
        self.session_count = len(sessions) if session_count is None else session_count
        self.item_count = len(items) if item_count is None else item_count
        if status_counts is None:
            status_counts = {}
            for item in items:
                status = item.get("status")
                if isinstance(status, str):
                    status_counts[status] = status_counts.get(status, 0) + 1
        self.status_counts = status_counts

    def get(self, path):
        if path == "/v1/ernie/sessions":
            return {
                "sessions": self.sessions,
                "count": self.session_count,
                "history_coverage": {
                    "complete": self.session_count < 25,
                    "receipt_id": f"ernie:sessions:{self.session_count}/25",
                    "candidates": [],
                },
            }
        if path == "/ik/ernie-dashboard/work-queue/status":
            return {
                "item_count": self.item_count,
                "status_counts": self.status_counts,
                "items": self.items,
            }
        raise AssertionError(path)


def test_completed_ready_item_is_not_open():
    item = {
        "status": "ready",
        "latest_outcome_decision": "completed",
        "latest_verification_decision": "passed",
        "latest_postcheck_decision": "passed",
    }
    assert queue_item_is_open(item) is False


def test_result_backed_ready_item_is_terminal_only_after_checks_pass():
    item = {
        "status": "ready",
        "latest_outcome_decision": "result-backed",
        "latest_verification_decision": "passed",
        "latest_postcheck_decision": "passed",
    }
    assert queue_item_is_open(item) is False
    item["latest_verification_decision"] = "failed"
    assert queue_item_is_open(item) is True


def test_ernie_is_idle_only_when_sources_succeed_and_nothing_is_open():
    result = collect_ernie_status(FakeClient([], []), NOW)
    assert result.status is WorkStatus.NO_PENDING_WORK


def test_ernie_reports_pending_for_a_real_ready_item():
    result = collect_ernie_status(
        FakeClient([], [{"item_id": "work-1", "status": "ready"}]),
        NOW,
    )
    assert result.status is WorkStatus.PENDING_WORK


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("draft", WorkStatus.PENDING_WORK),
        ("ready", WorkStatus.PENDING_WORK),
        ("in-progress", WorkStatus.PENDING_WORK),
        ("waiting", WorkStatus.PENDING_WORK),
        ("blocked", WorkStatus.PENDING_WORK),
        ("done", WorkStatus.NO_PENDING_WORK),
        ("archived", WorkStatus.NO_PENDING_WORK),
    ],
)
def test_every_queue_state_matches_the_production_contract(state, expected):
    result = collect_ernie_status(
        FakeClient([], [{"item_id": "work-1", "status": state}]),
        NOW,
    )
    assert result.status is expected


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("completed", WorkStatus.NO_PENDING_WORK),
        ("blocked", WorkStatus.PENDING_WORK),
        ("not_found", WorkStatus.NO_PENDING_WORK),
        ("needs_clarification", WorkStatus.PENDING_WORK),
        ("failed", WorkStatus.NO_PENDING_WORK),
    ],
)
def test_every_session_outcome_matches_the_production_contract(state, expected):
    result = collect_ernie_status(
        FakeClient(
            [
                {
                    "session_id": "session-1",
                    "latest_status": state,
                    "entry_count": 1,
                }
            ],
            [],
        ),
        NOW,
    )
    assert result.status is expected


def test_truncated_queue_aggregate_never_misses_older_pending_work():
    visible = [
        {"item_id": f"done-{value}", "status": "done"}
        for value in range(20)
    ]
    result = collect_ernie_status(
        FakeClient(
            [],
            visible,
            item_count=21,
            status_counts={"done": 20, "blocked": 1},
        ),
        NOW,
    )
    assert result.status is WorkStatus.PENDING_WORK
    assert "queue:blocked:1" in result.evidence


def test_truncated_unseen_ready_work_fails_closed_unknown():
    visible = [
        {"item_id": f"done-{value}", "status": "done"}
        for value in range(20)
    ]
    result = collect_ernie_status(
        FakeClient(
            [],
            visible,
            item_count=21,
            status_counts={"done": 20, "ready": 1},
        ),
        NOW,
    )
    assert result.status is WorkStatus.UNKNOWN


def test_stale_ready_item_is_clear_only_when_all_ready_rows_are_visible():
    item = {
        "item_id": "ready-1",
        "status": "ready",
        "latest_outcome_decision": "completed",
        "latest_verification_decision": "passed",
        "latest_postcheck_decision": "passed",
    }
    result = collect_ernie_status(FakeClient([], [item]), NOW)
    assert result.status is WorkStatus.NO_PENDING_WORK


@pytest.mark.parametrize(
    "overrides",
    [
        {"item_count": -1},
        {"item_count": 2},
        {"status_counts": {"done": -1}},
        {"status_counts": {"mystery": 1}},
    ],
)
def test_inconsistent_queue_aggregate_fails_closed(overrides):
    result = collect_ernie_status(
        FakeClient(
            [],
            [{"item_id": "done-1", "status": "done"}],
            **overrides,
        ),
        NOW,
    )
    assert result.status is WorkStatus.UNKNOWN


def test_session_cap_fails_closed_and_disables_history_ranking():
    sessions = [
        {
            "session_id": f"session-{value}",
            "latest_status": "completed",
            "entry_count": 1,
        }
        for value in range(25)
    ]
    result = collect_ernie_status(FakeClient(sessions, []), NOW)
    assert result.status is WorkStatus.UNKNOWN
    assert result.history_complete is False
    assert result.candidates == ()


def test_complete_sources_include_bounded_source_receipts():
    result = collect_ernie_status(FakeClient([], []), NOW)
    assert result.history_complete is True
    assert "ernie:sessions:0/25" in result.source_receipts
    assert "ernie:queue:0" in result.source_receipts


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


def test_attested_complete_bert_status_binds_candidates_to_source_receipt():
    content = json.dumps(
        {
            "status": "NO_PENDING_WORK",
            "summary": "No pending work in complete local receipts",
            "evidence": ["coverage:complete"],
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "title": "Audit scheduler health",
                    "category": "reliability",
                    "evidence": ["session:history-1"],
                    "impact": 4,
                    "recurrence": 3,
                    "confidence": 5,
                    "effort": 1,
                    "risk": 0,
                    "action_kind": "read_only_audit",
                    "recommended_owner": "ernie",
                    "executor_id": "scheduler-health",
                }
            ],
        }
    )
    result = collect_bert_status(FakeReadOnlyStatus(content), NOW)
    assert result.status is WorkStatus.NO_PENDING_WORK
    assert result.history_complete is True
    assert result.source_receipts[0].startswith("bert:no-tools:")
    assert result.candidates[0].evidence == ("session:history-1",)


def test_incomplete_bert_history_forces_unknown_even_if_model_claims_idle():
    content = json.dumps(
        {
            "status": "NO_PENDING_WORK",
            "summary": "claimed clear",
            "evidence": ["claimed evidence"],
            "candidates": [],
        }
    )
    result = collect_bert_status(
        FakeReadOnlyStatus(content, complete=False),
        NOW,
    )
    assert result.status is WorkStatus.UNKNOWN
    assert result.history_complete is False
    assert result.candidates == ()


def test_bert_model_cannot_override_authoritative_pending_status():
    content = json.dumps({
        "status": "NO_PENDING_WORK",
        "summary": "claimed clear",
        "evidence": ["coverage:complete"],
        "candidates": [],
    })
    fake = FakeReadOnlyStatus(content)
    original = fake.__call__

    def pending(*args, **kwargs):
        payload = json.loads(original(*args, **kwargs))
        payload["source_receipts"]["derived_status"] = {
            "status": "PENDING_WORK",
            "evidence_refs": ["session:history-1"],
        }
        payload["attestation"]["source_receipts_sha256"] = hashlib.sha256(
            _canonical(payload["source_receipts"])
        ).hexdigest()
        return json.dumps(payload)

    assert collect_bert_status(pending, NOW).status is WorkStatus.UNKNOWN


def test_tampered_bert_no_tools_attestation_forces_unknown():
    content = json.dumps(
        {
            "status": "NO_PENDING_WORK",
            "summary": "claimed clear",
            "evidence": ["claimed evidence"],
            "candidates": [],
        }
    )
    result = collect_bert_status(
        FakeReadOnlyStatus(content, tamper=True),
        NOW,
    )
    assert result.status is WorkStatus.UNKNOWN
    assert result.history_complete is False


def test_loopback_client_uses_endpoint_specific_timeout():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = b"{}"
    with patch(
        "shared_core.daily_goal_sources.urllib.request.urlopen",
        return_value=response,
    ) as urlopen:
        client = LoopbackJsonClient("http://127.0.0.1:7611")
        client.get("/v1/ernie/status")
        assert urlopen.call_args.kwargs["timeout"] == 50
        client.get("/ik/ernie-dashboard/work-queue/status")
        assert urlopen.call_args.kwargs["timeout"] == 10
