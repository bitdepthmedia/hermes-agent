"""Deterministic ranking and role allocation for daily improvement goals."""

from __future__ import annotations

from dataclasses import dataclass

from .daily_goal import ActionKind, AgentStatus, ImprovementCandidate


CATEGORY_WEIGHT = {
    "reliability": 35,
    "manual_work": 30,
    "tests": 25,
    "security": 25,
    "performance": 20,
    "docs": 15,
    "context": 10,
}


@dataclass(frozen=True)
class RankedCandidate:
    candidate: ImprovementCandidate
    score: int


def _valid(candidate: ImprovementCandidate) -> bool:
    values = (candidate.impact, candidate.recurrence, candidate.confidence, candidate.effort, candidate.risk)
    return (
        candidate.category in CATEGORY_WEIGHT
        and bool(candidate.evidence)
        and all(0 <= value <= 5 for value in values)
        and candidate.recommended_owner in {"bert", "ernie"}
        and bool(candidate.executor_id)
    )


def _score(candidate: ImprovementCandidate) -> int:
    return (
        CATEGORY_WEIGHT[candidate.category]
        + candidate.impact * 5
        + candidate.recurrence * 3
        + candidate.confidence * 2
        - candidate.effort * 2
        - candidate.risk * 5
    )


def rank_candidates(statuses: tuple[AgentStatus, ...]) -> list[RankedCandidate]:
    unique: dict[str, ImprovementCandidate] = {}
    for status in statuses:
        if not status.history_complete:
            continue
        for candidate in status.candidates:
            if _valid(candidate):
                unique.setdefault(candidate.candidate_id, candidate)
    ranked = [RankedCandidate(value, _score(value)) for value in unique.values()]
    return sorted(ranked, key=lambda value: (-value.score, value.candidate.candidate_id))


def select_goal(statuses: tuple[AgentStatus, ...]) -> RankedCandidate | None:
    ranked = rank_candidates(statuses)
    return ranked[0] if ranked else None


def assign_roles(candidate: ImprovementCandidate) -> tuple[str, str]:
    owner = candidate.recommended_owner
    if owner == "bert" and candidate.action_kind is not ActionKind.READ_ONLY_AUDIT:
        owner = "ernie"
    return (owner, "bert" if owner == "ernie" else "ernie")
