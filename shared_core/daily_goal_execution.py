"""Fail-closed fixed-GET execution and counterpart review for daily goals."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Callable

from .daily_goal import ActionKind, ImprovementCandidate
from .daily_goal_sources import load_no_tools_attestation


ALLOWED_EXECUTORS = {
    "system-health",
    "gateway-dashboard",
    "scheduler-health",
    "documentation-draft",
    "patch-proposal",
}
ERNIE_GET_CAPABILITIES = {
    (ActionKind.READ_ONLY_AUDIT, "system-health"): (
        "/health",
        "/v1/ernie/status",
    ),
    (ActionKind.READ_ONLY_AUDIT, "scheduler-health"): (
        "/v1/ernie/sessions",
        "/ik/ernie-dashboard/work-queue/status",
    ),
}
BLOCKED_TEXT = {
    "deploy",
    "production",
    "live mutation",
    "install ",
    "upgrade ",
    "credential",
    "secret",
    "delete",
    "rm -rf",
    "git push",
    "publish",
    "http://",
    "https://",
    "shell",
    "curl ",
    "wget ",
    "ignore previous",
    "ignore all instructions",
    "system prompt",
    "developer message",
    "tool call",
    "jailbreak",
    "```",
    "$(",
}
_SUMMARY_PATTERNS = {
    "system-health": re.compile(
        r"^system-health audit: health_status=ok; "
        r"offline_capable=(true|false); services_up=(\d{1,12})/(\d{1,12})$"
    ),
    "scheduler-health": re.compile(
        r"^scheduler-health audit: session_count=(\d{1,12}); "
        r"queue_item_count=(\d{1,12}); queue_status_buckets=(\d{1,12})$"
    ),
}


@dataclass(frozen=True)
class ExecutionOutcome:
    ok: bool
    actor: str
    summary: str
    evidence: tuple[str, ...]
    blocker: str | None = None
    review_statement: str | None = None
    review_hash: str | None = None
    review_source: str | None = None
    review_metrics_hash: str | None = None

    @property
    def reviewer(self) -> str:
        """Compatibility name for the actor that supplied a review outcome."""
        return self.actor


def _validate_text(text: str) -> None:
    lowered = text.lower()
    if any(token in lowered for token in BLOCKED_TEXT):
        raise ValueError("candidate requires approval-gated action")


def _validate(candidate: ImprovementCandidate) -> tuple[ActionKind, str]:
    if candidate.executor_id not in ALLOWED_EXECUTORS:
        raise ValueError(f"unsupported executor: {candidate.executor_id}")
    if not isinstance(candidate.action_kind, ActionKind):
        raise ValueError("unsupported action kind")
    _validate_text(candidate.title)
    _validate_text(" ".join(candidate.evidence))
    key = (candidate.action_kind, candidate.executor_id)
    if key not in ERNIE_GET_CAPABILITIES:
        raise ValueError("action/executor combination is not allowlisted")
    return key


def _validate_owner(owner: str) -> None:
    if owner not in {"bert", "ernie"}:
        raise ValueError(f"unsupported owner: {owner}")


def _is_nonnegative_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _system_health_summary(payloads: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    health = payloads["/health"]
    runtime = payloads["/v1/ernie/status"]
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise ValueError("malformed health payload")
    if not isinstance(runtime, dict) or not isinstance(runtime.get("offline_capable"), bool):
        raise ValueError("malformed runtime payload")
    services = runtime.get("services")
    if (
        not isinstance(services, dict)
        or not services
        or not all(isinstance(name, str) and isinstance(value, bool) for name, value in services.items())
    ):
        raise ValueError("malformed services payload")

    offline_capable = "true" if runtime["offline_capable"] else "false"
    services_up = sum(1 for value in services.values() if value)
    services_total = len(services)
    summary = (
        "system-health audit: health_status=ok; "
        f"offline_capable={offline_capable}; services_up={services_up}/{services_total}"
    )
    evidence = (
        "/health:status=ok",
        (
            "/v1/ernie/status:"
            f"offline_capable={offline_capable};services_up={services_up}/{services_total}"
        ),
    )
    return summary, evidence


def _scheduler_health_summary(payloads: dict[str, object]) -> tuple[str, tuple[str, ...]]:
    sessions_payload = payloads["/v1/ernie/sessions"]
    queue_payload = payloads["/ik/ernie-dashboard/work-queue/status"]
    if not isinstance(sessions_payload, dict):
        raise ValueError("malformed sessions payload")
    sessions = sessions_payload.get("sessions")
    session_count = sessions_payload.get("count")
    if (
        not isinstance(sessions, list)
        or not all(isinstance(session, dict) for session in sessions)
        or not _is_nonnegative_int(session_count)
        or session_count < len(sessions)
    ):
        raise ValueError("malformed sessions payload")

    if not isinstance(queue_payload, dict):
        raise ValueError("malformed queue payload")
    queue_items = queue_payload.get("items")
    queue_count = queue_payload.get("item_count")
    status_counts = queue_payload.get("status_counts")
    if (
        not isinstance(queue_items, list)
        or not all(isinstance(item, dict) for item in queue_items)
        or not _is_nonnegative_int(queue_count)
        or queue_count < len(queue_items)
        or not isinstance(status_counts, dict)
        or not all(
            isinstance(status, str)
            and bool(status)
            and _is_nonnegative_int(count)
            for status, count in status_counts.items()
        )
        or sum(status_counts.values()) != queue_count
    ):
        raise ValueError("malformed queue payload")

    status_buckets = len(status_counts)
    summary = (
        f"scheduler-health audit: session_count={session_count}; "
        f"queue_item_count={queue_count}; queue_status_buckets={status_buckets}"
    )
    evidence = (
        f"/v1/ernie/sessions:count={session_count}",
        (
            "/ik/ernie-dashboard/work-queue/status:"
            f"item_count={queue_count};status_buckets={status_buckets}"
        ),
    )
    return summary, evidence


def _run_ernie_audit(ernie, key: tuple[ActionKind, str]) -> ExecutionOutcome:
    payloads: dict[str, object] = {}
    try:
        for path in ERNIE_GET_CAPABILITIES[key]:
            payloads[path] = ernie.get(path)
    except Exception:
        return ExecutionOutcome(False, "ernie", "", (), "Ernie fixed GET audit failed")

    try:
        if key[1] == "system-health":
            summary, evidence = _system_health_summary(payloads)
        else:
            summary, evidence = _scheduler_health_summary(payloads)
    except (KeyError, TypeError, ValueError):
        return ExecutionOutcome(
            False,
            "ernie",
            "",
            (),
            "Ernie fixed GET audit returned a malformed payload",
        )
    return ExecutionOutcome(True, "ernie", summary, evidence)


def _validate_execution_summary(executor_id: str, summary: str) -> None:
    if not isinstance(summary, str) or len(summary) > 240:
        raise ValueError("review requires a bounded fixed-GET summary")
    pattern = _SUMMARY_PATTERNS.get(executor_id)
    match = pattern.fullmatch(summary) if pattern else None
    if not match:
        raise ValueError("review requires a bounded fixed-GET summary")
    if executor_id == "system-health" and int(match.group(2)) > int(match.group(3)):
        raise ValueError("review requires a bounded fixed-GET summary")


def _valid_review_statement(content: object) -> bool:
    if (
        not isinstance(content, str)
        or len(content) > 600
        or "\n" in content
        or "\r" in content
    ):
        return False
    if content != content.strip() or len(content) < 20:
        return False
    try:
        _validate_text(content)
    except ValueError:
        return False
    return True


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


def execute_goal(
    candidate: ImprovementCandidate,
    *,
    owner: str,
    ernie,
    call_orchestrator: Callable[..., str],
) -> ExecutionOutcome:
    key = _validate(candidate)
    _validate_owner(owner)
    if owner == "bert":
        return ExecutionOutcome(
            False,
            "bert",
            "",
            (),
            "Bert automatic execution lacks a technically constrained read-only adapter",
        )
    return _run_ernie_audit(ernie, key)


def review_goal(
    candidate: ImprovementCandidate,
    *,
    owner: str,
    execution_summary: str,
    ernie,
    call_orchestrator: Callable[..., str],
) -> ExecutionOutcome:
    _validate(candidate)
    _validate_owner(owner)
    if owner == "bert":
        return ExecutionOutcome(
            False,
            "ernie",
            "",
            (),
            "Ernie counterpart review is unavailable for blocked Bert execution",
        )

    _validate_execution_summary(candidate.executor_id, execution_summary)
    execution_sha256 = _sha256(execution_summary)
    source_content = {
        "candidate_id": candidate.candidate_id,
        "executor_id": candidate.executor_id,
        "owner": owner,
        "execution_summary": execution_summary,
        "execution_sha256": execution_sha256,
    }
    source_receipt = {
        "content": source_content,
        "sha256": _sha256(_canonical_bytes(source_content)),
    }
    review_prompt = (
        "Review only the supplied, attested fixed-GET audit receipt. Return one "
        "JSON object with exactly these keys: decision, candidate_id, executor_id, "
        "execution_sha256, statement. decision must be pass or fail. Bind all IDs "
        "and the execution hash exactly to the receipt. statement must be one "
        "substantive evidence sentence. "
        f"candidate_id={candidate.candidate_id}; "
        f"executor_id={candidate.executor_id}; "
        f"execution_sha256={execution_sha256}"
    )
    try:
        outer = load_no_tools_attestation(
            call_orchestrator(
                review_prompt,
                purpose="review",
                source_receipt=source_receipt,
                max_tokens=400,
            ),
            input_text=review_prompt,
            purpose="review",
            source_receipt=source_receipt,
            max_tokens=400,
        )
        data = json.loads(outer["content"])
        if not isinstance(data, dict) or set(data) != {
            "decision",
            "candidate_id",
            "executor_id",
            "execution_sha256",
            "statement",
        }:
            raise ValueError("review response shape mismatch")
        statement = data["statement"]
        if (
            data["decision"] not in {"pass", "fail"}
            or data["candidate_id"] != candidate.candidate_id
            or data["executor_id"] != candidate.executor_id
            or data["execution_sha256"] != execution_sha256
            or not _valid_review_statement(statement)
        ):
            raise ValueError("review response binding mismatch")
    except Exception:
        return ExecutionOutcome(False, "bert", "", (), "Bert review failed")
    review_source = f"bert-no-tools:{source_receipt['sha256']}"
    review_hash = _sha256(
        _canonical_bytes(
            {
                "metrics_hash": execution_sha256,
                "source": review_source,
                "statement": statement,
            }
        )
    )
    evidence = (
        "bert:no-tools-review",
        review_source,
    )
    if data["decision"] == "fail":
        return ExecutionOutcome(
            False,
            "bert",
            statement,
            evidence,
            "Bert review rejected execution",
            review_statement=statement,
            review_hash=review_hash,
            review_source=review_source,
            review_metrics_hash=execution_sha256,
        )
    return ExecutionOutcome(
        True,
        "bert",
        statement,
        evidence,
        review_statement=statement,
        review_hash=review_hash,
        review_source=review_source,
        review_metrics_hash=execution_sha256,
    )
