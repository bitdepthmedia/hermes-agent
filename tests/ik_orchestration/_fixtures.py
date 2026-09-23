from __future__ import annotations

from copy import deepcopy


def envelope_value(**changes: object) -> dict:
    value = {
        "schema_version": "1.0",
        "task_id": "11111111-1111-4111-8111-111111111111",
        "parent_task_id": None,
        "owner": "ernie",
        "requester_persona": "ernie",
        "task_class": "work",
        "privacy_class": "sanitized-cloud",
        "payload": {"request": "Prepare a safe work packet"},
        "local_payload_ref": "ernie-local:packet-1",
        "provenance": {"channel": "fixture", "message_id": "m-1"},
        "constraints": {"forbidden_actions": ["deploy"]},
        "approval": {"state": "not-required", "scope": []},
        "expected_result": {"schema_id": "ik.result.summary.v1"},
        "completion": "pending",
        "idempotency_key": "work:m-1",
        "lineage": {"hop_count": 0, "max_hops": 4, "visited_owners": ["ernie"], "prior_digest": None},
        "retry": {"attempt": 0, "next_attempt_at": None, "expires_at": "2026-08-22T00:00:00Z", "last_ack_sequence": 0, "escalation": "approval-inbox"},
        "integrity": {"sender": "ernie", "sequence": 1, "signature_metadata": "fixture", "envelope_digest": None},
    }
    value.update(deepcopy(changes))
    return value
