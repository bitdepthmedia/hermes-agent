"""Evidence-gated improvement candidates without authority expansion."""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter


@dataclass(frozen=True)
class SafeReceipt:
    task_id: str
    parent_id: str
    pattern: str
    successful: bool
    retry: bool


@dataclass(frozen=True)
class LearningPolicy:
    minimum_independent_successes: int = 3


@dataclass(frozen=True)
class ImprovementCandidate:
    pattern: str
    evidence_task_ids: tuple[str, ...]


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    evidence_count: int


PROTECTED_EFFECTS = frozenset({"dependency", "permission", "schedule", "write", "external", "cloud", "privacy", "authority"})


def detect_candidate(receipts: list[SafeReceipt], policy: LearningPolicy) -> ImprovementCandidate | None:
    valid = [item for item in receipts if item.successful and not item.retry]
    counts = Counter(item.pattern for item in valid)
    for pattern, count in sorted(counts.items()):
        independent = {item.parent_id for item in valid if item.pattern == pattern}
        if count >= policy.minimum_independent_successes and len(independent) >= policy.minimum_independent_successes:
            return ImprovementCandidate(pattern, tuple(item.task_id for item in valid if item.pattern == pattern))
    return None


def promotion_decision(candidate: ImprovementCandidate | None, *, validated: bool, effects: frozenset[str]) -> PromotionDecision:
    if candidate is None or not validated:
        return PromotionDecision("rejected", 0 if candidate is None else len(candidate.evidence_task_ids))
    status = "approval_required" if effects & PROTECTED_EFFECTS else "auto-enabled"
    return PromotionDecision(status, len(candidate.evidence_task_ids))
