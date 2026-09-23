"""Thin Nate OS memory boundary adapter; policy remains in Nate OS."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class MemoryAction(StrEnum):
    READ = "read"
    PROPOSE = "propose"
    WRITE = "write"


@dataclass(frozen=True)
class BoundaryDecision:
    allowed: bool
    code: str


def authorize_memory_action(agent_id: str, action: MemoryAction, visibility: str) -> BoundaryDecision:
    if action == MemoryAction.WRITE:
        return BoundaryDecision(False, "canonical-write-denied")
    if agent_id == "bert":
        allowed = action == MemoryAction.READ and visibility == "all-agents"
        return BoundaryDecision(allowed, "bert-all-agents-read-only" if allowed else "bert-boundary-denied")
    if agent_id == "ernie":
        allowed = action in {MemoryAction.READ, MemoryAction.PROPOSE} and visibility in {"all-agents", "ernie-local"}
        return BoundaryDecision(allowed, "ernie-proposal-boundary" if allowed else "ernie-boundary-denied")
    allowed = action == MemoryAction.READ and visibility == "all-agents"
    return BoundaryDecision(allowed, "unknown-read-only" if allowed else "unknown-boundary-denied")
