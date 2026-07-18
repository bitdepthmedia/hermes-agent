"""Fail-closed execution and counterpart review for daily improvement goals."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from .daily_goal import ActionKind, ImprovementCandidate


ALLOWED_EXECUTORS = {
    "system-health",
    "gateway-dashboard",
    "scheduler-health",
    "documentation-draft",
    "patch-proposal",
}
ERNIE_CAPABILITIES = {
    (ActionKind.READ_ONLY_AUDIT, "system-health"):
        "DAILY_GOAL_AUDIT: report bounded local system-health evidence only.",
    (ActionKind.READ_ONLY_AUDIT, "scheduler-health"):
        "DAILY_GOAL_AUDIT: report bounded local scheduler-health evidence only.",
}
BLOCKED_TEXT = {
    "deploy", "production", "live mutation", "install ", "upgrade ",
    "credential", "secret", "delete", "rm -rf", "git push", "publish",
    "http://", "https://", "shell", "curl ", "wget ", "ignore previous",
    "ignore all instructions", "system prompt", "developer message", "tool call",
    "jailbreak", "```", "$(`", "$(",
}
_BERT_REVIEW_PASS = re.compile(r"^REVIEW_PASS:\s*(\S.+)$")


@dataclass(frozen=True)
class ExecutionOutcome:
    ok: bool
    actor: str
    summary: str
    evidence: tuple[str, ...]
    blocker: str | None = None

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
    if key not in ERNIE_CAPABILITIES:
        raise ValueError("action/executor combination is not allowlisted")
    return key


def _validate_owner(owner: str) -> None:
    if owner not in {"bert", "ernie"}:
        raise ValueError(f"unsupported owner: {owner}")


def _load_orchestrator_response(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _ernie_receipt(response: object, *, review: bool) -> tuple[bool, str]:
    if not isinstance(response, dict):
        return False, ""
    content = str(response.get("assistant_response") or "").strip()
    verification = response.get("verification")
    tool_trace = response.get("tool_trace")
    clean_receipt = (
        response.get("ok") is True
        and response.get("mode") == "dry_run"
        and isinstance(verification, dict)
        and verification.get("decision") == "passed"
        and response.get("files_touched") == []
        and response.get("backups_created") == []
        and response.get("limits_or_refusals") == []
        and isinstance(tool_trace, list)
        and all(
            isinstance(step, dict)
            and step.get("status") in {"completed", "dry_run"}
            and "write" not in str(step.get("name") or "")
            for step in tool_trace
        )
    )
    if not clean_receipt or not content:
        return False, content
    if review and len(content) < 20:
        return False, content
    return True, content


def _ernie_call(ernie, message: str, *, review: bool) -> ExecutionOutcome:
    actor = "ernie"
    failure = "Ernie review failed" if review else "Ernie execution failed"
    verification_failure = "Ernie review failed" if review else "Ernie execution verification failed"
    try:
        response = ernie.post(
            "/api/ernie/agent/run",
            {"message": message, "mode": "dry_run"},
        )
    except Exception:
        return ExecutionOutcome(False, actor, "", (), failure)
    ok, content = _ernie_receipt(response, review=review)
    if not ok:
        return ExecutionOutcome(False, actor, content[:800], (), verification_failure)
    return ExecutionOutcome(
        True,
        actor,
        content[:800],
        ("ernie:review",) if review else ("ernie:read-only-audit",),
    )


def execute_goal(
    candidate: ImprovementCandidate,
    *,
    owner: str,
    ernie,
    call_orchestrator: Callable[..., str],
) -> ExecutionOutcome:
    key = _validate(candidate)
    _validate_owner(owner)
    if owner == "ernie":
        return _ernie_call(ernie, ERNIE_CAPABILITIES[key], review=False)

    try:
        raw = _load_orchestrator_response(call_orchestrator(
            task=(
                "Perform the selected allowlisted read-only local audit and return "
                "non-empty evidence only. Do not mutate live or local state."
            ),
            max_tokens=1000,
        ))
    except Exception:
        raw = None
    content = str((raw or {}).get("content") or "").strip()
    if not raw or raw.get("success") is not True or not content:
        return ExecutionOutcome(False, "bert", "", (), "Bert audit failed")
    return ExecutionOutcome(True, "bert", content[:1000], ("bert:read-only-audit",))


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
    _validate_text(execution_summary)
    if owner == "ernie":
        try:
            raw = _load_orchestrator_response(call_orchestrator(
                task=(
                    "Review this completed allowlisted local audit. Reply exactly "
                    "REVIEW_PASS: followed by one evidence sentence, or REVIEW_FAIL: "
                    f"followed by one evidence sentence. Evidence: {execution_summary[:1200]}"
                ),
                max_tokens=500,
            ))
        except Exception:
            raw = None
        content = str((raw or {}).get("content") or "").strip()
        if not raw or raw.get("success") is not True or not _BERT_REVIEW_PASS.fullmatch(content):
            return ExecutionOutcome(False, "bert", content[:800], (), "Bert review failed")
        return ExecutionOutcome(True, "bert", content[:800], ("bert:review",))

    return _ernie_call(
        ernie,
        f"DAILY_GOAL_REVIEW: assess this bounded audit evidence only: {execution_summary[:1200]}",
        review=True,
    )
