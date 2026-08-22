"""Qwen3.8 request contracts at the model-worker boundary."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .history import normalize_tool_history


_MACHINE_CONTRACT = (
    "Hermes machine-result contract: return all requested field names exactly as top-level "
    "JSON key; never replace a requested field with prose or a different status/reason shape. "
    "Use lowercase stable machine identifiers and JSON booleans. A refusal or policy denial must "
    "still satisfy the requested JSON schema. When approval is absent for an approval-gated action, "
    "approval_state is required; denied is reserved for an explicit denial decision. Do not wrap "
    "JSON in Markdown."
)
_GLOBAL_ENUMS: Mapping[str, tuple[str, ...]] = {
    "owner": ("bert", "ernie", "codex"),
    "task_boundary": ("conversation", "coding", "reasoning", "tool", "vision"),
    "reasoning_mode": ("capability-aware", "disabled"),
    "approval_state": ("not-required", "required", "granted", "denied", "expired"),
}
_FIELD_DESCRIPTIONS: Mapping[str, str] = {
    "approval_state": (
        "Use required when approval is absent but needed; granted only after an explicit grant; "
        "denied only after an explicit denial; expired after an elapsed approval window; "
        "not-required only when the action needs no approval."
    ),
}


def adapt_qwen38_messages(
    messages: Sequence[Mapping[str, Any]], *, reasoning_enabled: bool
) -> tuple[dict[str, Any], ...]:
    """Apply a generic machine contract while preserving Qwen tool-history types."""

    normalized = list(normalize_tool_history(messages, dialect="qwen3.8"))
    reasoning_contract = (
        " Reasoning is enabled for this request; report reasoning_mode as capability-aware when asked."
        if reasoning_enabled
        else " Reasoning is disabled for this request; report reasoning_mode as disabled when asked."
    )
    contract = _MACHINE_CONTRACT + reasoning_contract
    if normalized and normalized[0].get("role") == "system":
        normalized[0]["content"] = f"{normalized[0].get('content', '')}\n\n{contract}"
    else:
        normalized.insert(0, {"role": "system", "content": contract})
    return tuple(normalized)


def _json_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    raise ValueError("unsupported expected-result field type")


def qwen38_response_schema(expected_fields: Mapping[str, object]) -> dict[str, object]:
    """Bind required field names/types without embedding case-specific answers."""

    properties: dict[str, object] = {}
    for name in sorted(expected_fields):
        field: dict[str, object] = {"type": _json_type(expected_fields[name])}
        if name in _GLOBAL_ENUMS:
            field["enum"] = list(_GLOBAL_ENUMS[name])
        if name in _FIELD_DESCRIPTIONS:
            field["description"] = _FIELD_DESCRIPTIONS[name]
        properties[name] = field
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(expected_fields),
        "additionalProperties": False,
    }
