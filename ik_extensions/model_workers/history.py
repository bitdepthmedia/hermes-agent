"""Canonical OpenAI-compatible tool history normalization."""

from __future__ import annotations

from copy import deepcopy
import json
from typing import Mapping, Any, Sequence


def normalize_tool_history(
    messages: Sequence[Mapping[str, Any]], *, dialect: str = "openai"
) -> tuple[dict[str, Any], ...]:
    normalized = deepcopy([dict(message) for message in messages])
    call_ids: set[str] = set()
    for message in normalized:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls", []):
            function = call.get("function", {})
            arguments = function.get("arguments")
            if isinstance(arguments, Mapping):
                parsed = dict(arguments)
            elif isinstance(arguments, str):
                parsed = json.loads(arguments)
            else:
                raise ValueError("tool call arguments must be JSON string or mapping")
            if not isinstance(parsed, Mapping):
                raise ValueError("tool call arguments must decode to a mapping")
            if dialect == "qwen3.8":
                function["arguments"] = dict(parsed)
            elif dialect == "openai":
                function["arguments"] = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
            else:
                raise ValueError("unsupported tool history dialect")
            call_ids.add(str(call.get("id")))
    for message in normalized:
        if message.get("role") == "tool" and str(message.get("tool_call_id")) not in call_ids:
            raise ValueError("orphan tool result")
    return tuple(normalized)
