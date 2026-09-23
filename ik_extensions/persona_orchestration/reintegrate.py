"""Local-only validation and reintegration of sanitized task results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class LocalMappingStore:
    def __init__(self) -> None:
        self._values: dict[str, Mapping[str, Any]] = {}

    def put(self, mapping_id: str, value: Mapping[str, Any]) -> None:
        self._values[mapping_id] = dict(value)

    def get(self, mapping_id: str) -> Mapping[str, Any]:
        if mapping_id not in self._values:
            raise ValueError("local mapping not found")
        return dict(self._values[mapping_id])


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    payload_digest: str
    recipient: str
    completion: str
    schema_id: str
    result: Mapping[str, Any]


@dataclass(frozen=True)
class ReintegratedResult:
    result: Mapping[str, Any]
    local_mapping: Mapping[str, Any]


def reintegrate_local(result: TaskResult, store: LocalMappingStore, *, expected_payload_digest: str, mapping_id: str) -> ReintegratedResult:
    if result.payload_digest != expected_payload_digest or result.recipient != "ernie" or result.completion != "completed":
        raise ValueError("result binding mismatch")
    if result.result.get("request_local_mapping") or result.result.get("expanded_authority"):
        raise ValueError("result attempted to obtain local mapping or expand authority")
    return ReintegratedResult(dict(result.result), store.get(mapping_id))
