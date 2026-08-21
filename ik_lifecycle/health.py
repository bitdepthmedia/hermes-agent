from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class HealthEvidence:
    REQUIRED = ("receipt_digest", "profile_generation", "endpoint", "heartbeat", "tool_task", "router_disclosure", "kanban", "cron", "profile_isolation", "nate_os", "messaging", "restart", "backup")
    gates: Mapping[str, str]
    runtime_sha: str
    code_sha: str
    legacy_automation_status: str


@dataclass(frozen=True)
class HealthGateSet:
    status: str
    blockers: tuple[str, ...]


def verify_cell(evidence: HealthEvidence) -> HealthGateSet:
    blockers = [name for name in HealthEvidence.REQUIRED if evidence.gates.get(name) != "CLEAR"]
    if evidence.runtime_sha != evidence.code_sha: blockers.append("runtime-code-parity")
    if evidence.legacy_automation_status != "PAUSED": blockers.append("legacy-automation-pause-approval")
    return HealthGateSet("BLOCKED" if blockers else "CLEAR", tuple(blockers))
