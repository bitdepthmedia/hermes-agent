from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from ik_extensions.persona_orchestration.envelope import (
    LifecycleContractError,
    Owner,
    OwnershipEvent,
    canonical_digest,
    transfer_owner,
    validate_envelope,
)


def envelope_fixture() -> dict:
    return {
        "schema_version": "1.0",
        "task_id": "11111111-1111-4111-8111-111111111111",
        "parent_task_id": None,
        "owner": "ernie",
        "requester_persona": "ernie",
        "task_class": "work",
        "privacy_class": "sanitized-cloud",
        "payload": {"request": "Prepare the redacted work packet"},
        "local_payload_ref": "ernie-local:packet-1",
        "provenance": {"channel": "fixture", "message_id": "m-1", "sanitizer_version": "1"},
        "constraints": {"forbidden_actions": ["deploy"], "output_limit": 500},
        "approval": {"state": "not-required", "scope": []},
        "expected_result": {"schema_id": "ik.result.summary.v1", "acceptance": "typed summary"},
        "completion": "pending",
        "idempotency_key": "work:m-1",
        "lineage": {"hop_count": 0, "max_hops": 4, "visited_owners": ["ernie"], "prior_digest": None},
        "retry": {"attempt": 0, "next_attempt_at": None, "expires_at": None, "last_ack_sequence": 0, "escalation": "none"},
        "integrity": {"sender": "ernie", "sequence": 1, "signature_metadata": "fixture", "envelope_digest": None},
        "minor_extension": {"preserve": True},
    }


class EnvelopeContractTests(unittest.TestCase):
    def test_required_fields_and_unknown_major_fail_closed(self) -> None:
        for required in envelope_fixture():
            if required == "minor_extension":
                continue
            value = envelope_fixture()
            value.pop(required)
            with self.subTest(required=required), self.assertRaises(LifecycleContractError):
                validate_envelope(value)

        value = envelope_fixture()
        value["schema_version"] = "2.0"
        with self.assertRaisesRegex(LifecycleContractError, "major"):
            validate_envelope(value)

    def test_minor_extensions_round_trip_and_digest_is_stable(self) -> None:
        envelope = validate_envelope(envelope_fixture())

        self.assertEqual(envelope.to_dict()["minor_extension"], {"preserve": True})
        self.assertEqual(canonical_digest(envelope), "d0a91e73627bc51d94b1a47d60086722fa42a0018c9b8e849fdcbedea6e516ed")
        self.assertEqual(canonical_digest(validate_envelope(envelope.to_dict())), canonical_digest(envelope))

    def test_local_private_payload_cannot_target_bert(self) -> None:
        value = envelope_fixture()
        value["owner"] = "bert"
        value["privacy_class"] = "local-private"
        value["payload"] = {"private": "SYNTHETIC_PRIVATE_CANARY"}

        with self.assertRaisesRegex(LifecycleContractError, "local-private"):
            validate_envelope(value)

    def test_owner_transfer_preserves_request_and_appends_history(self) -> None:
        source = validate_envelope(envelope_fixture())
        transferred = transfer_owner(
            source,
            OwnershipEvent(from_owner=Owner.ERNIE, to_owner=Owner.CODEX, reason="work-owner", at="2026-08-21T22:10:00Z"),
        )

        self.assertEqual(transferred.owner, Owner.CODEX)
        self.assertEqual(transferred.task_id, source.task_id)
        self.assertEqual(transferred.payload, source.payload)
        self.assertEqual(transferred.provenance, source.provenance)
        self.assertEqual(transferred.to_dict()["ownership_events"], [{"from_owner":"ernie","to_owner":"codex","reason":"work-owner","at":"2026-08-21T22:10:00Z"}])
        self.assertEqual(transferred.lineage["hop_count"], 1)
        self.assertEqual(transferred.lineage["visited_owners"], ["ernie", "codex"])

    def test_transfer_rejects_wrong_source_loop_and_terminal_completion(self) -> None:
        source = validate_envelope(envelope_fixture())
        wrong = OwnershipEvent(from_owner=Owner.BERT, to_owner=Owner.CODEX, reason="wrong", at="2026-08-21T22:10:00Z")
        with self.assertRaisesRegex(LifecycleContractError, "source owner"):
            transfer_owner(source, wrong)

        loop = OwnershipEvent(from_owner=Owner.ERNIE, to_owner=Owner.ERNIE, reason="loop", at="2026-08-21T22:10:00Z")
        with self.assertRaisesRegex(LifecycleContractError, "loop"):
            transfer_owner(source, loop)

        terminal_value = envelope_fixture()
        terminal_value["completion"] = "completed"
        terminal = validate_envelope(terminal_value)
        with self.assertRaisesRegex(LifecycleContractError, "terminal"):
            transfer_owner(terminal, OwnershipEvent(Owner.ERNIE, Owner.CODEX, "late", "2026-08-21T22:10:00Z"))

    def test_hop_overflow_and_task_id_mutation_fail(self) -> None:
        overflow = envelope_fixture()
        overflow["lineage"]["hop_count"] = 5
        with self.assertRaisesRegex(LifecycleContractError, "hop"):
            validate_envelope(overflow)

        invalid_id = envelope_fixture()
        invalid_id["task_id"] = "changed"
        with self.assertRaisesRegex(LifecycleContractError, "UUID"):
            validate_envelope(invalid_id)


class ContractFixtureTests(unittest.TestCase):
    def test_contract_documents_are_json_and_require_wire_fields(self) -> None:
        root = Path(__file__).resolve().parents[2]
        contract_root = root / "ik_extensions/persona_orchestration/contracts"
        required = {
            "delegation-envelope-v1.json": {"task_id", "owner", "privacy_class", "idempotency_key", "lineage", "integrity"},
            "task-result-v1.json": {"task_id", "payload_digest", "recipient", "completion", "result"},
            "transport-ack-v1.json": {"task_id", "sequence", "envelope_digest", "recipient", "status"},
        }
        for filename, fields in required.items():
            document = json.loads((contract_root / filename).read_text(encoding="utf-8"))
            self.assertEqual(document["type"], "object")
            self.assertTrue(fields.issubset(set(document["required"])))


if __name__ == "__main__":
    unittest.main()
