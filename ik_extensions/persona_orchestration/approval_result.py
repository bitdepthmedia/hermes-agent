"""Typed approval-result semantics shared by runtimes and model adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ApprovalContractError(ValueError):
    """Fail-closed approval-result contract error."""


class ApprovalState(StrEnum):
    REQUIRED = "required"
    APPROVED = "approved"
    DENIED = "denied"
    NOT_REQUIRED = "not_required"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    DENY = "deny"


@dataclass(frozen=True)
class ApprovalResult:
    approval_state: ApprovalState
    executed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.approval_state, ApprovalState) or not isinstance(self.executed, bool):
            raise ApprovalContractError("approval result types are invalid")
        if self.executed and self.approval_state not in {
            ApprovalState.APPROVED,
            ApprovalState.NOT_REQUIRED,
        }:
            raise ApprovalContractError("execution requires effective authority")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": "1.0",
            "approval_state": self.approval_state.value,
            "executed": self.executed,
        }


def resolve_approval_result(
    *,
    approval_required: bool,
    decision: ApprovalDecision | None,
    executed: bool,
) -> ApprovalResult:
    """Resolve policy facts without asking a model to invent approval authority."""

    if not isinstance(approval_required, bool) or not isinstance(executed, bool):
        raise ApprovalContractError("approval facts must be booleans")
    if decision is not None and not isinstance(decision, ApprovalDecision):
        raise ApprovalContractError("approval decision is invalid")
    if not approval_required:
        if decision is not None:
            raise ApprovalContractError("approval decision conflicts with not-required policy")
        state = ApprovalState.NOT_REQUIRED
    elif decision is None:
        state = ApprovalState.REQUIRED
    elif decision == ApprovalDecision.APPROVE:
        state = ApprovalState.APPROVED
    else:
        state = ApprovalState.DENIED
    return ApprovalResult(approval_state=state, executed=executed)


def approval_state_property() -> dict[str, object]:
    """Return the model-neutral JSON Schema property for an approval result."""

    return {
        "type": "string",
        "enum": [state.value for state in ApprovalState],
        "description": (
            "required means policy requires approval and no approval decision is recorded; "
            "approved means an explicit in-scope grant is recorded; denied means an explicit "
            "refusal is recorded; not_required means policy requires no approval."
        ),
    }


def approval_result_instruction() -> str:
    """Return adapter-neutral semantics for machine approval results."""

    return (
        "Typed approval result: required = approval is required and there is no recorded approval "
        "decision; a request to proceed without asking is not approval and does not become denied. "
        "approved = an explicit in-scope grant is recorded. denied = an explicit refusal is recorded. "
        "not_required = policy requires no approval. required and denied always have executed=false."
    )
