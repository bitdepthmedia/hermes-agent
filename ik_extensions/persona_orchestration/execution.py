"""Cheapest-safe execution rung selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import hashlib


class ExecutionRung(IntEnum):
    INLINE = 1
    TOOL = 2
    WORKFLOW = 3
    SUBAGENT = 4
    DURABLE = 5


@dataclass(frozen=True)
class Capability:
    capability_id: str
    rung: ExecutionRung
    permissions: frozenset[str]
    recurring: bool


@dataclass(frozen=True)
class ExecutionDecision:
    rung: ExecutionRung
    capability_id: str
    background_handle: str
    persona_remains_available: bool = True


def choose_execution_rung(required_permissions: frozenset[str], recurring: bool, catalog: tuple[Capability, ...]) -> ExecutionDecision:
    candidates = [item for item in catalog if required_permissions.issubset(item.permissions) and (not recurring or item.recurring)]
    if not candidates:
        raise ValueError("no capability can execute without authority expansion")
    chosen = min(candidates, key=lambda item: (item.rung, item.capability_id))
    handle = hashlib.sha256(f"{chosen.capability_id}:{sorted(required_permissions)}:{recurring}".encode()).hexdigest()[:12]
    return ExecutionDecision(chosen.rung, chosen.capability_id, f"bg:{handle}")
