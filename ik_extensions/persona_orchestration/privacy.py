"""Directional, allowlist-first sanitization for cloud recipients."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Mapping, Any

from .envelope import Owner


@dataclass(frozen=True)
class LocalTask:
    task_id: str
    fields: Mapping[str, Any]


@dataclass(frozen=True)
class PrivacyPolicy:
    allowed_fields: tuple[str, ...]
    version: str


@dataclass(frozen=True)
class SanitizedTask:
    task_id: str
    recipient: Owner
    payload: Mapping[str, Any]
    payload_digest: str
    local_mapping_id: str
    receipt: Mapping[str, Any]


def sanitize_for_recipient(source: LocalTask, recipient: Owner, policy: PrivacyPolicy) -> SanitizedTask:
    if recipient == Owner.ERNIE:
        raise ValueError("cross-zone sanitizer is for cloud recipients")
    payload = {key: source.fields[key] for key in policy.allowed_fields if key in source.fields}
    blocked_markers = ("private_canary", "synthetic_secret", "token=", "/private/", "/users/")
    for value in payload.values():
        if isinstance(value, str) and any(marker in value.casefold() for marker in blocked_markers):
            raise ValueError("allowed field contains sensitive value; task remains local")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(encoded).hexdigest()
    mapping_id = f"ernie-local:{hashlib.sha256(source.task_id.encode()).hexdigest()[:16]}"
    receipt = {
        "policy_version": policy.version,
        "payload_digest": digest,
        "local_mapping_id": mapping_id,
        "removed_field_count": len(set(source.fields) - set(payload)),
    }
    return SanitizedTask(source.task_id, recipient, payload, digest, mapping_id, receipt)
