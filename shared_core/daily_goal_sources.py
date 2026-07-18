"""Strict local evidence collectors for the daily-goal coordinator."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timedelta
from typing import Callable
from urllib.parse import urlparse

from .daily_goal import ActionKind, AgentStatus, ImprovementCandidate, WorkStatus


class LoopbackJsonClient:
    """Small JSON client restricted to local HTTP status endpoints."""

    def __init__(self, base_url: str, timeout: float = 10):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("daily goal source must use loopback http")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(self.base_url + path, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))


OPEN_QUEUE_STATES = {"ready", "in-progress", "waiting"}
OPEN_SESSION_STATES = {"pending", "in-progress", "running", "waiting", "blocked"}
TERMINAL_QUEUE_STATES = {"completed", "failed", "cancelled", "canceled", "skipped"}
TERMINAL_SESSION_STATES = {
    "completed",
    "failed",
    "error",
    "cancelled",
    "canceled",
    "stopped",
}


def _has_known_status(record: object, field: str, known_states: set[str]) -> bool:
    value = record.get(field) if isinstance(record, dict) else None
    return isinstance(value, str) and value.lower() in known_states


def queue_item_is_open(item: dict) -> bool:
    if str(item.get("status") or "").lower() not in OPEN_QUEUE_STATES:
        return False
    completed = item.get("latest_outcome_decision") == "completed"
    verified = item.get("latest_verification_decision") == "passed"
    postchecked = item.get("latest_postcheck_decision") == "passed"
    return not (completed and verified and postchecked)


def collect_ernie_status(client: LoopbackJsonClient, now: datetime) -> AgentStatus:
    try:
        sessions = list(client.get("/v1/ernie/sessions").get("sessions") or [])
        items = list(client.get("/ik/ernie-dashboard/work-queue/list").get("items") or [])
    except Exception as exc:
        return AgentStatus(
            "ernie",
            WorkStatus.UNKNOWN,
            "Ernie status unavailable",
            (type(exc).__name__,),
            now.isoformat(),
            (),
        )

    if any(
        not _has_known_status(
            item, "status", OPEN_QUEUE_STATES | TERMINAL_QUEUE_STATES
        )
        for item in items
    ) or any(
        not _has_known_status(
            row, "latest_status", OPEN_SESSION_STATES | TERMINAL_SESSION_STATES
        )
        for row in sessions
    ):
        return AgentStatus(
            "ernie",
            WorkStatus.UNKNOWN,
            "Ernie status contains an unrecognized state",
            ("invalid-status",),
            now.isoformat(),
            (),
        )

    open_items = [item for item in items if queue_item_is_open(item)]
    open_sessions = [
        row
        for row in sessions
        if str(row.get("latest_status") or "").lower() in OPEN_SESSION_STATES
    ]
    cutoff = now - timedelta(days=7)
    recent_failures = []
    for row in sessions:
        raw_updated = row.get("updated_at")
        try:
            updated = datetime.fromtimestamp(float(raw_updated), tz=now.tzinfo)
        except (TypeError, ValueError, OSError):
            continue
        if updated >= cutoff and str(row.get("latest_status") or "").lower() in {
            "failed",
            "error",
        }:
            recent_failures.append(row)
    candidates = tuple(
        ImprovementCandidate(
            candidate_id=f"ernie-session-{row.get('session_id', 'unknown')}",
            title=f"Investigate failed Ernie session {row.get('session_id', 'unknown')}",
            category="reliability",
            evidence=(f"session:{row.get('session_id', 'unknown')}:{row.get('latest_status')}",),
            impact=3,
            recurrence=1,
            confidence=5,
            effort=2,
            risk=0,
            action_kind=ActionKind.READ_ONLY_AUDIT,
            recommended_owner="ernie",
            executor_id="system-health",
        )
        for row in recent_failures[:5]
    )
    evidence = tuple(
        [f"queue:{row.get('item_id', 'unknown')}" for row in open_items[:5]]
        + [
            f"session:{row.get('session_id', 'unknown')}:{row.get('latest_status')}"
            for row in open_sessions[:5]
        ]
    )
    if open_items or open_sessions:
        return AgentStatus(
            "ernie",
            WorkStatus.PENDING_WORK,
            "Ernie has verified pending work",
            evidence,
            now.isoformat(),
            candidates,
        )
    return AgentStatus(
        "ernie",
        WorkStatus.NO_PENDING_WORK,
        "Ernie has no verified pending work",
        ("queue:clear", "sessions:clear"),
        now.isoformat(),
        candidates,
    )


BERT_STATUS_TASK = """Review your previous seven days of task history, your current
tracker state, and explicitly unresolved older improvement items.
Return one JSON object only:
{"status":"PENDING_WORK|NO_PENDING_WORK|UNKNOWN","summary":"short",
"evidence":["explicit current evidence"],
"candidates":[{"candidate_id":"stable-id","title":"sanitized improvement",
"category":"reliability|manual_work|tests|security|performance|docs|context",
"evidence":["explicit evidence"],"impact":0,"recurrence":0,"confidence":0,
"effort":0,"risk":0,"action_kind":"read_only_audit|focused_test|documentation_draft|patch_proposal",
"recommended_owner":"bert|ernie","executor_id":"bounded identifier"}]}
Use integers 0-5. Project work is pending only with explicit evidence it remains open.
If evidence is missing or ambiguous, return UNKNOWN. Do not perform work."""


def _candidate(data: dict) -> ImprovementCandidate:
    return ImprovementCandidate(
        candidate_id=str(data["candidate_id"])[:80],
        title=str(data["title"])[:160],
        category=str(data["category"]),
        evidence=tuple(str(value)[:240] for value in data.get("evidence") or ()),
        impact=int(data["impact"]),
        recurrence=int(data["recurrence"]),
        confidence=int(data["confidence"]),
        effort=int(data["effort"]),
        risk=int(data["risk"]),
        action_kind=ActionKind(data["action_kind"]),
        recommended_owner=str(data["recommended_owner"]),
        executor_id=str(data["executor_id"])[:80],
    )


def collect_bert_status(call_orchestrator: Callable[..., str], now: datetime) -> AgentStatus:
    try:
        outer = json.loads(call_orchestrator(task=BERT_STATUS_TASK, max_tokens=1200))
        if outer.get("success") is not True:
            raise ValueError(str(outer.get("error") or "orchestrator failed"))
        data = json.loads(str(outer["content"]))
        status = WorkStatus(data["status"])
        evidence = tuple(str(value)[:240] for value in data.get("evidence") or ())
        if status is not WorkStatus.UNKNOWN and not evidence:
            raise ValueError("non-unknown Bert status requires evidence")
        candidates = tuple(_candidate(value) for value in data.get("candidates") or ())
        return AgentStatus(
            "bert",
            status,
            str(data.get("summary") or "")[:240],
            evidence,
            now.isoformat(),
            candidates,
        )
    except Exception as exc:
        return AgentStatus(
            "bert",
            WorkStatus.UNKNOWN,
            "Bert status unavailable",
            (type(exc).__name__,),
            now.isoformat(),
            (),
        )
