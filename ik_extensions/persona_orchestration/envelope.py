"""Stable delegation envelope used across routing, persistence and transport."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from enum import StrEnum
from typing import Any, Mapping
from uuid import UUID


class LifecycleContractError(ValueError):
    """Fail-closed contract validation error."""


class Owner(StrEnum):
    BERT = "bert"
    ERNIE = "ernie"
    CODEX = "codex"


class PrivacyClass(StrEnum):
    PUBLIC = "public"
    SANITIZED_CLOUD = "sanitized-cloud"
    LOCAL_PRIVATE = "local-private"
    SECRET_PROHIBITED = "secret-prohibited"


class TaskClass(StrEnum):
    PERSONAL = "personal"
    WORK = "work"
    MIXED = "mixed"
    MIXED_CHILD = "mixed-child"


COMPLETION_STATES = {
    "pending",
    "accepted",
    "running",
    "waiting",
    "completed",
    "failed",
    "expired",
    "cancelled",
}
TERMINAL_STATES = {"completed", "failed", "expired", "cancelled"}
REQUIRED_FIELDS = (
    "schema_version",
    "task_id",
    "parent_task_id",
    "owner",
    "requester_persona",
    "task_class",
    "privacy_class",
    "payload",
    "local_payload_ref",
    "provenance",
    "constraints",
    "approval",
    "expected_result",
    "completion",
    "idempotency_key",
    "lineage",
    "retry",
    "integrity",
)


@dataclass(frozen=True)
class DelegationEnvelope:
    schema_version: str
    task_id: str
    parent_task_id: str | None
    owner: Owner
    requester_persona: Owner
    task_class: TaskClass
    privacy_class: PrivacyClass
    payload: Mapping[str, Any]
    local_payload_ref: str | None
    provenance: Mapping[str, Any]
    constraints: Mapping[str, Any]
    approval: Mapping[str, Any]
    expected_result: Mapping[str, Any]
    completion: str
    idempotency_key: str
    lineage: Mapping[str, Any]
    retry: Mapping[str, Any]
    integrity: Mapping[str, Any]
    _raw: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(dict(self._raw))


@dataclass(frozen=True)
class OwnershipEvent:
    from_owner: Owner
    to_owner: Owner
    reason: str
    at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "from_owner": self.from_owner.value,
            "to_owner": self.to_owner.value,
            "reason": self.reason,
            "at": self.at,
        }


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise LifecycleContractError(f"{field} must be an object")
    return value


def _uuid(value: Any, field: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str):
        raise LifecycleContractError(f"{field} must be a UUID string")
    try:
        UUID(value)
    except ValueError as exc:
        raise LifecycleContractError(f"{field} must be a UUID string") from exc
    return value


def validate_envelope(value: Mapping[str, object]) -> DelegationEnvelope:
    if not isinstance(value, Mapping):
        raise LifecycleContractError("envelope must be an object")
    missing = [field for field in REQUIRED_FIELDS if field not in value]
    if missing:
        raise LifecycleContractError(f"missing required fields: {', '.join(missing)}")
    schema_version = value["schema_version"]
    if not isinstance(schema_version, str) or "." not in schema_version:
        raise LifecycleContractError("schema_version must be major.minor")
    major, minor = schema_version.split(".", 1)
    if major != "1" or not minor.isdigit():
        raise LifecycleContractError("unsupported schema major version")
    try:
        owner = Owner(value["owner"])
        requester = Owner(value["requester_persona"])
        task_class = TaskClass(value["task_class"])
        privacy = PrivacyClass(value["privacy_class"])
    except (ValueError, TypeError) as exc:
        raise LifecycleContractError("invalid owner, requester, task, or privacy class") from exc
    task_id = _uuid(value["task_id"], "task_id")
    parent_task_id = _uuid(value["parent_task_id"], "parent_task_id", optional=True)
    completion = value["completion"]
    if completion not in COMPLETION_STATES:
        raise LifecycleContractError("invalid completion state")
    idempotency_key = value["idempotency_key"]
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise LifecycleContractError("idempotency_key is required")
    payload = _mapping(value["payload"], "payload")
    local_ref = value["local_payload_ref"]
    if local_ref is not None and not isinstance(local_ref, str):
        raise LifecycleContractError("local_payload_ref must be a string or null")
    if privacy == PrivacyClass.LOCAL_PRIVATE and owner != Owner.ERNIE:
        raise LifecycleContractError("local-private payload may only target Ernie")
    if privacy == PrivacyClass.SECRET_PROHIBITED and payload:
        raise LifecycleContractError("secret-prohibited tasks cannot carry a payload")
    lineage = _mapping(value["lineage"], "lineage")
    hop_count = lineage.get("hop_count")
    max_hops = lineage.get("max_hops")
    visited = lineage.get("visited_owners")
    if not isinstance(hop_count, int) or not isinstance(max_hops, int) or hop_count < 0 or max_hops < 1:
        raise LifecycleContractError("lineage hop values are invalid")
    if hop_count > max_hops:
        raise LifecycleContractError("lineage hop overflow")
    if not isinstance(visited, list) or not visited or not all(item in Owner._value2member_map_ for item in visited):
        raise LifecycleContractError("lineage visited owners are invalid")
    if visited[-1] != owner.value:
        raise LifecycleContractError("lineage owner does not match current owner")
    raw = deepcopy(dict(value))
    return DelegationEnvelope(
        schema_version=schema_version,
        task_id=task_id,
        parent_task_id=parent_task_id,
        owner=owner,
        requester_persona=requester,
        task_class=task_class,
        privacy_class=privacy,
        payload=deepcopy(dict(payload)),
        local_payload_ref=local_ref,
        provenance=deepcopy(dict(_mapping(value["provenance"], "provenance"))),
        constraints=deepcopy(dict(_mapping(value["constraints"], "constraints"))),
        approval=deepcopy(dict(_mapping(value["approval"], "approval"))),
        expected_result=deepcopy(dict(_mapping(value["expected_result"], "expected_result"))),
        completion=str(completion),
        idempotency_key=idempotency_key,
        lineage=deepcopy(dict(lineage)),
        retry=deepcopy(dict(_mapping(value["retry"], "retry"))),
        integrity=deepcopy(dict(_mapping(value["integrity"], "integrity"))),
        _raw=raw,
    )


def canonical_digest(envelope: DelegationEnvelope) -> str:
    value = envelope.to_dict()
    integrity = dict(value["integrity"])
    integrity.pop("envelope_digest", None)
    value["integrity"] = integrity
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def transfer_owner(envelope: DelegationEnvelope, event: OwnershipEvent) -> DelegationEnvelope:
    if event.from_owner != envelope.owner:
        raise LifecycleContractError("ownership event source owner does not match")
    if event.to_owner == event.from_owner or event.to_owner.value in envelope.lineage["visited_owners"]:
        raise LifecycleContractError("ownership loop rejected")
    if envelope.completion in TERMINAL_STATES:
        raise LifecycleContractError("terminal envelope ownership cannot change")
    value = envelope.to_dict()
    value["owner"] = event.to_owner.value
    events = list(value.get("ownership_events", []))
    events.append(event.to_dict())
    value["ownership_events"] = events
    lineage = dict(value["lineage"])
    lineage["hop_count"] = int(lineage["hop_count"]) + 1
    lineage["prior_digest"] = canonical_digest(envelope)
    lineage["visited_owners"] = [*lineage["visited_owners"], event.to_owner.value]
    value["lineage"] = lineage
    integrity = dict(value["integrity"])
    integrity["sender"] = event.from_owner.value
    integrity["envelope_digest"] = None
    value["integrity"] = integrity
    return validate_envelope(value)
