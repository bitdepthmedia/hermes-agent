"""Allowlisted execution and counterpart review for daily improvement goals."""

from __future__ import annotations

import json
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
BLOCKED_TEXT = {
    "deploy", "production", "live mutation", "install ", "upgrade ",
    "credential", "secret", "delete", "rm -rf", "git push", "publish",
    "http://", "https://", "shell", "curl ", "wget ",
}


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


def _validate(candidate: ImprovementCandidate) -> None:
    if candidate.executor_id not in ALLOWED_EXECUTORS:
        raise ValueError(f"unsupported executor: {candidate.executor_id}")
    if candidate.action_kind not in ActionKind:
        raise ValueError("unsupported action kind")
    combined = f"{candidate.title} {' '.join(candidate.evidence)}".lower()
    if any(token in combined for token in BLOCKED_TEXT):
        raise ValueError("candidate requires approval-gated action")


def _validate_owner(owner: str) -> None:
    if owner not in {"bert", "ernie"}:
        raise ValueError(f"unsupported owner: {owner}")


def execute_goal(
    candidate: ImprovementCandidate,
    *,
    owner: str,
    ernie,
    call_orchestrator: Callable[..., str],
) -> ExecutionOutcome:
    _validate(candidate)
    _validate_owner(owner)
    if owner == "bert":
        if candidate.action_kind is not ActionKind.READ_ONLY_AUDIT:
            raise ValueError("Bert automatic ownership is read-only")
        raw = json.loads(call_orchestrator(
            task=(
                "Perform a read-only audit for this goal and return evidence only: "
                f"{candidate.title}. Do not mutate live or local state."
            ),
            max_tokens=1000,
        ))
        if raw.get("success") is not True:
            return ExecutionOutcome(False, "bert", "", (), str(raw.get("error") or "Bert audit failed"))
        return ExecutionOutcome(
            True,
            "bert",
            str(raw.get("content") or "")[:1000],
            ("bert:read-only-audit",),
        )

    if candidate.action_kind is ActionKind.FOCUSED_TEST:
        if candidate.executor_id != "gateway-dashboard":
            raise ValueError("focused test is not allowlisted")
        response = ernie.post("/v1/ernie/ask", {"prompt": "Run the local Ernie dashboard tests."})
    else:
        mode = "auto" if candidate.action_kind is ActionKind.READ_ONLY_AUDIT else "dry_run"
        response = ernie.post(
            "/api/ernie/agent/run",
            {
                "message": f"{candidate.action_kind.value}: {candidate.title}",
                "mode": mode,
            },
        )
    answer = str(response.get("answer") or "")[:1000]
    ok = bool(answer) and "blocked" not in answer.lower()
    return ExecutionOutcome(
        ok,
        "ernie",
        answer,
        (f"ernie:{candidate.executor_id}",),
        None if ok else "Ernie execution blocked",
    )


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
    if owner == "ernie":
        raw = json.loads(call_orchestrator(
            task=(
                "Review this completed low-risk local improvement. "
                "Reply REVIEW_PASS or REVIEW_FAIL followed by one evidence sentence. "
                f"Goal: {candidate.title}. Result: {execution_summary[:1200]}"
            ),
            max_tokens=500,
        ))
        content = str(raw.get("content") or "")
        evidence = content.removeprefix("REVIEW_PASS").strip(" :.-")
        ok = raw.get("success") is True and content.startswith("REVIEW_PASS") and bool(evidence)
        return ExecutionOutcome(
            ok,
            "bert",
            content[:800],
            ("bert:review",) if ok else (),
            None if ok else "Bert review failed",
        )

    response = ernie.post(
        "/api/ernie/agent/run",
        {
            "message": (
                f"Read-only review of Bert result for {candidate.title}: "
                f"{execution_summary[:1200]}"
            ),
            "mode": "dry_run",
        },
    )
    content = str(response.get("answer") or "")
    ok = bool(content)
    return ExecutionOutcome(
        ok,
        "ernie",
        content[:800],
        ("ernie:review",) if ok else (),
        None if ok else "Ernie review failed",
    )
