"""Strict local evidence collectors for the daily-goal coordinator."""

from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Callable
from urllib.parse import urlparse

from .daily_goal import ActionKind, AgentStatus, ImprovementCandidate, WorkStatus


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def load_no_tools_attestation(
    raw: str,
    *,
    input_text: str,
    purpose: str,
    max_tokens: int,
    source_receipt: dict | None = None,
) -> dict:
    """Validate a dedicated read-only response independently of its client."""
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or parsed.get("success") is not True:
        raise ValueError("no-tools response did not report success")
    content = parsed.get("content")
    receipts = parsed.get("source_receipts")
    attestation = parsed.get("attestation")
    if (
        not isinstance(content, str)
        or not isinstance(receipts, dict)
        or not isinstance(attestation, dict)
    ):
        raise ValueError("no-tools response is missing attested content")
    fixed = {
        "mode": "no_tools",
        "enabled_toolsets": [],
        "tool_names": [],
        "tool_calls": 0,
    }
    if any(attestation.get(key) != value for key, value in fixed.items()):
        raise ValueError("no-tools attestation contains tool capability")

    payload = {
        "purpose": purpose,
        "input": input_text,
        "max_tokens": max_tokens,
    }
    if source_receipt is not None:
        payload["source_receipt"] = source_receipt
    expected_hashes = {
        "request_sha256": _sha256(_canonical_bytes(payload)),
        "input_sha256": _sha256(input_text),
        "output_sha256": _sha256(content),
        "source_receipts_sha256": _sha256(_canonical_bytes(receipts)),
    }
    if any(
        attestation.get(key) != expected
        for key, expected in expected_hashes.items()
    ):
        raise ValueError("no-tools attestation digest mismatch")
    if receipts.get("purpose") != purpose or not isinstance(
        receipts.get("items"), list
    ):
        raise ValueError("no-tools source receipt purpose mismatch")
    if purpose == "review":
        items = receipts["items"]
        if (
            len(items) != 1
            or items[0].get("kind") != "caller_source_receipt"
            or {
                "content": items[0].get("content"),
                "sha256": items[0].get("sha256"),
            }
            != source_receipt
        ):
            raise ValueError("review source receipt mismatch")
    return parsed


class LoopbackJsonClient:
    """Small JSON client restricted to local HTTP status endpoints."""

    def __init__(
        self,
        base_url: str,
        timeout: float = 10,
        timeout_by_path: dict[str, float] | None = None,
    ):
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("daily goal source must use loopback http")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.timeout_by_path = {
            "/v1/ernie/status": 50,
            **(timeout_by_path or {}),
        }

    def _timeout_for(self, path: str) -> float:
        return self.timeout_by_path.get(path.split("?", 1)[0], self.timeout)

    def get(self, path: str) -> dict:
        with urllib.request.urlopen(
            self.base_url + path,
            timeout=self._timeout_for(path),
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def post(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(
            request,
            timeout=self._timeout_for(path),
        ) as response:
            return json.loads(response.read().decode("utf-8"))


QUEUE_STATES = {
    "draft",
    "ready",
    "in-progress",
    "waiting",
    "done",
    "blocked",
    "archived",
}
DIRECT_PENDING_QUEUE_STATES = {"draft", "in-progress", "waiting", "blocked"}
SESSION_STATES = {
    "completed",
    "blocked",
    "not_found",
    "needs_clarification",
    "failed",
}
PENDING_SESSION_STATES = {"blocked", "needs_clarification"}
QUEUE_VISIBLE_LIMIT = 20
SESSION_LIMIT = 25
SESSION_ENTRY_LIMIT = 40


def queue_item_is_open(item: dict) -> bool:
    if str(item.get("status") or "").lower() not in {
        "draft",
        "ready",
        "in-progress",
        "waiting",
        "blocked",
    }:
        return False
    completed = item.get("latest_outcome_decision") in {
        "completed",
        "result-backed",
    }
    verified = item.get("latest_verification_decision") == "passed"
    postchecked = item.get("latest_postcheck_decision") == "passed"
    return not (completed and verified and postchecked)


def _valid_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _queue_evidence(payload: object) -> tuple[list[str], list[str], tuple[str, ...]]:
    """Return pending evidence, uncertainty evidence, and source receipts."""
    if not isinstance(payload, dict):
        return [], ["queue:invalid-payload"], ()
    item_count = payload.get("item_count")
    counts = payload.get("status_counts")
    items = payload.get("items")
    if (
        not _valid_count(item_count)
        or not isinstance(counts, dict)
        or not isinstance(items, list)
    ):
        return [], ["queue:invalid-aggregate"], ()
    if any(
        state not in QUEUE_STATES or not _valid_count(count)
        for state, count in counts.items()
    ):
        return [], ["queue:invalid-counts"], ()
    if sum(counts.values()) != item_count:
        return [], ["queue:inconsistent-count-total"], ()
    expected_visible = min(item_count, QUEUE_VISIBLE_LIMIT)
    if len(items) != expected_visible:
        return [], ["queue:incomplete-visible-page"], ()

    visible_counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            return [], ["queue:invalid-item"], ()
        state = item.get("status")
        if (
            not isinstance(state, str)
            or state not in QUEUE_STATES
            or not isinstance(item.get("item_id"), str)
            or not item["item_id"]
        ):
            return [], ["queue:invalid-item"], ()
        visible_counts[state] = visible_counts.get(state, 0) + 1
    if any(
        visible_count > counts.get(state, 0)
        for state, visible_count in visible_counts.items()
    ):
        return [], ["queue:visible-count-exceeds-aggregate"], ()

    pending = [
        f"queue:{state}:{counts.get(state, 0)}"
        for state in sorted(DIRECT_PENDING_QUEUE_STATES)
        if counts.get(state, 0) > 0
    ]
    visible_ready = [
        item
        for item in items
        if item["status"] == "ready"
    ]
    if any(queue_item_is_open(item) for item in visible_ready):
        pending.extend(
            f"queue:{item['item_id']}:ready"
            for item in visible_ready
            if queue_item_is_open(item)
        )
    unknown: list[str] = []
    if counts.get("ready", 0) > len(visible_ready):
        unknown.append("queue:unseen-ready")
    receipts = (f"ernie:queue:{item_count}",)
    return pending, unknown, receipts


def _session_evidence(
    payload: object,
    now: datetime,
) -> tuple[list[str], list[str], bool, tuple[str, ...]]:
    """Return pending evidence, uncertainty, completeness, and source receipts."""
    if not isinstance(payload, dict):
        return [], ["sessions:invalid-payload"], False, ()
    sessions = payload.get("sessions")
    count = payload.get("count")
    if (
        not isinstance(sessions, list)
        or not _valid_count(count)
        or count != len(sessions)
        or count > SESSION_LIMIT
    ):
        return [], ["sessions:invalid-count"], False, ()

    pending: list[str] = []
    unknown: list[str] = []
    timestamps: list[datetime] = []
    cap_complete = count < SESSION_LIMIT
    entries_complete = True
    for row in sessions:
        if not isinstance(row, dict):
            unknown.append("sessions:invalid-row")
            continue
        session_id = row.get("session_id")
        state = row.get("latest_status")
        entry_count = row.get("entry_count")
        updated_at = row.get("updated_at")
        if (
            not isinstance(session_id, str)
            or not session_id
            or not isinstance(state, str)
            or state not in SESSION_STATES
            or not _valid_count(entry_count)
            or entry_count > SESSION_ENTRY_LIMIT
        ):
            unknown.append("sessions:invalid-row")
            continue
        try:
            parsed = (
                datetime.fromtimestamp(float(updated_at), tz=UTC)
                if isinstance(updated_at, (int, float))
                and not isinstance(updated_at, bool)
                else datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            )
            if parsed.tzinfo is None:
                raise ValueError
            timestamps.append(parsed.astimezone(UTC))
        except (TypeError, ValueError, OverflowError):
            unknown.append("sessions:invalid-timestamp")
        if entry_count == SESSION_ENTRY_LIMIT:
            entries_complete = False
        if state in PENDING_SESSION_STATES:
            pending.append(f"session:{session_id}:{state}")
    if count == SESSION_LIMIT:
        cutoff = now.astimezone(UTC) - timedelta(days=7)
        cap_complete = bool(timestamps) and min(timestamps) <= cutoff
    timestamps_complete = len(timestamps) == count
    history_complete = cap_complete and timestamps_complete and entries_complete
    if not history_complete:
        unknown.append("sessions:history-incomplete")
    receipt_digest = _sha256(_canonical_bytes({"count": count, "sessions": sessions}))
    receipts = (f"ernie:sessions:sha256:{receipt_digest}",)
    return pending, unknown, history_complete, receipts


def collect_ernie_status(client: LoopbackJsonClient, now: datetime) -> AgentStatus:
    queue_payload: object
    session_payload: object
    source_errors: list[str] = []
    try:
        session_payload = client.get("/v1/ernie/sessions")
    except Exception as exc:
        session_payload = None
        source_errors.append(f"sessions:{type(exc).__name__}")
    try:
        queue_payload = client.get("/ik/ernie-dashboard/work-queue/status")
    except Exception as exc:
        queue_payload = None
        source_errors.append(f"queue:{type(exc).__name__}")

    queue_pending, queue_unknown, queue_receipts = _queue_evidence(
        queue_payload
    )
    (
        session_pending,
        session_unknown,
        history_complete,
        session_receipts,
    ) = _session_evidence(session_payload, now)
    pending = queue_pending + session_pending
    unknown = source_errors + queue_unknown + session_unknown
    receipts = session_receipts + queue_receipts
    candidates: tuple[ImprovementCandidate, ...] = ()
    if history_complete and isinstance(session_payload, dict):
        record_refs = {
            f"session:{row.get('session_id')}"
            for row in session_payload.get("sessions") or ()
            if isinstance(row, dict) and row.get("session_id")
        }
        try:
            cutoff = now.astimezone(UTC) - timedelta(days=7)
            candidates = tuple(
                ImprovementCandidate(
                    f"ernie-session-{row['session_id']}"[:80],
                    f"Audit follow-up for Ernie session {row['session_id']}"[:160],
                    "reliability",
                    (f"session:{row['session_id']}",),
                    2, 1, 3, 1, 0,
                    ActionKind.READ_ONLY_AUDIT,
                    "ernie",
                    "scheduler-health",
                )
                for row in session_payload.get("sessions") or ()
                if isinstance(row, dict)
                and (
                    (
                        row.get("latest_status") == "completed"
                        and datetime.fromtimestamp(
                            float(row["updated_at"]), tz=UTC
                        ) >= cutoff
                        and datetime.fromtimestamp(
                            float(row["updated_at"]), tz=UTC
                        ) <= now.astimezone(UTC)
                    )
                    or (
                        row.get("latest_status") in PENDING_SESSION_STATES
                        and datetime.fromtimestamp(
                            float(row["updated_at"]), tz=UTC
                        ) <= now.astimezone(UTC)
                    )
                )
                and row.get("latest_files_changed")
            )
        except Exception:
            unknown.append("sessions:invalid-candidate-attestation")

    if pending:
        return AgentStatus(
            "ernie",
            WorkStatus.PENDING_WORK,
            "Ernie has verified pending work",
            tuple(pending[:10]),
            now.isoformat(),
            candidates,
            history_complete=history_complete and not source_errors,
            source_receipts=receipts,
        )
    if unknown:
        return AgentStatus(
            "ernie",
            WorkStatus.UNKNOWN,
            "Ernie status is incomplete or inconsistent",
            tuple(unknown[:10]),
            now.isoformat(),
            (),
            history_complete=False,
            source_receipts=receipts,
        )
    return AgentStatus(
        "ernie",
        WorkStatus.NO_PENDING_WORK,
        "Ernie has no verified pending work",
        ("queue:clear", "sessions:clear"),
        now.isoformat(),
        candidates,
        history_complete=history_complete,
        source_receipts=receipts,
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


def _candidate(
    data: dict,
    *,
    allowed_record_refs: set[str],
) -> ImprovementCandidate:
    evidence = tuple(str(value)[:240] for value in data.get("evidence") or ())
    if not evidence or not set(evidence).issubset(allowed_record_refs):
        raise ValueError("candidate evidence is not bound to attested records")
    return ImprovementCandidate(
        candidate_id=str(data["candidate_id"])[:80],
        title=str(data["title"])[:160],
        category=str(data["category"]),
        evidence=evidence,
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
    source_receipt_id = ""
    try:
        raw = call_orchestrator(
            BERT_STATUS_TASK,
            purpose="status",
            max_tokens=1200,
        )
        outer = load_no_tools_attestation(
            raw,
            input_text=BERT_STATUS_TASK,
            purpose="status",
            max_tokens=1200,
        )
        receipts = outer["source_receipts"]
        source_digest = outer["attestation"]["source_receipts_sha256"]
        source_receipt_id = f"bert:no-tools:{source_digest}"
        items = receipts["items"]
        coverage = receipts.get("coverage")
        derived = receipts.get("derived_status")
        kinds = {
            item.get("kind")
            for item in items
            if isinstance(item, dict)
        }
        item_by_kind = {
            item.get("kind"): item
            for item in items
            if isinstance(item, dict)
        }
        cron_item = item_by_kind.get("cron_metadata") or {}
        if (
            len(items) != 2
            or kinds != {"session_db_metadata", "cron_metadata"}
            or not isinstance(coverage, dict)
            or (
                coverage.get("complete") is not True
                and (
                    not isinstance(derived, dict)
                    or derived.get("status") != "PENDING_WORK"
                )
            )
            or not isinstance(derived, dict)
            or derived.get("status") not in {
                "PENDING_WORK",
                "NO_PENDING_WORK",
            }
            or not isinstance(derived.get("evidence_refs"), list)
            or cron_item.get("available") is not True
            or not isinstance(cron_item.get("pagination"), dict)
            or cron_item["pagination"].get("complete") is not True
            or cron_item["pagination"].get("truncated") is not False
        ):
            return AgentStatus(
                "bert",
                WorkStatus.UNKNOWN,
                "Bert status history is incomplete",
                (source_receipt_id,),
                now.isoformat(),
                (),
                history_complete=False,
                source_receipts=(source_receipt_id,),
            )
        data = json.loads(str(outer["content"]))
        status = WorkStatus(data["status"])
        if status.value != derived["status"]:
            raise ValueError("model status conflicts with authoritative receipt")
        evidence = tuple(str(value)[:240] for value in data.get("evidence") or ())
        derived_refs = {
            str(value)[:240] for value in derived["evidence_refs"]
            if isinstance(value, str) and value
        }
        if not evidence or set(evidence) != derived_refs:
            raise ValueError("model evidence conflicts with authoritative receipt")
        record_refs = {
            f"{'session' if item['kind'] == 'session_db_metadata' else 'cron'}:"
            f"{record.get('id')}"
            for item in items
            for record in item.get("records") or ()
            if isinstance(record, dict) and record.get("id")
        }
        history_complete = coverage.get("complete") is True
        candidates = (
            tuple(
                _candidate(value, allowed_record_refs=record_refs)
                for value in data.get("candidates") or ()
            )
            if history_complete
            else ()
        )
        return AgentStatus(
            "bert",
            status,
            str(data.get("summary") or "")[:240],
            evidence,
            now.isoformat(),
            candidates,
            history_complete=history_complete,
            source_receipts=(source_receipt_id,),
        )
    except Exception as exc:
        return AgentStatus(
            "bert",
            WorkStatus.UNKNOWN,
            "Bert status unavailable",
            (type(exc).__name__,),
            now.isoformat(),
            (),
            history_complete=False,
            source_receipts=(
                (source_receipt_id,) if source_receipt_id else ()
            ),
        )
