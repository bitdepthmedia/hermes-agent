from __future__ import annotations

import json
from pathlib import Path
import unittest

from ik_extensions.persona_orchestration.approval_result import (
    ApprovalContractError,
    ApprovalDecision,
    ApprovalState,
    approval_result_instruction,
    approval_state_property,
    resolve_approval_result,
)


class ApprovalResultContractTests(unittest.TestCase):
    def test_resolver_distinguishes_absent_approval_from_explicit_decisions(self) -> None:
        cases = (
            (True, None, False, "required"),
            (True, ApprovalDecision.APPROVE, False, "approved"),
            (True, ApprovalDecision.DENY, False, "denied"),
            (False, None, False, "not_required"),
        )
        for required, decision, executed, expected in cases:
            with self.subTest(expected=expected):
                result = resolve_approval_result(
                    approval_required=required,
                    decision=decision,
                    executed=executed,
                )
                self.assertEqual(result.approval_state.value, expected)
                self.assertEqual(
                    result.to_dict(),
                    {"schema_version": "1.0", "approval_state": expected, "executed": executed},
                )

    def test_resolver_fails_closed_on_execution_without_effective_authority(self) -> None:
        for decision in (None, ApprovalDecision.DENY):
            with self.subTest(decision=decision), self.assertRaises(ApprovalContractError):
                resolve_approval_result(
                    approval_required=True,
                    decision=decision,
                    executed=True,
                )

    def test_contract_uses_only_canonical_model_result_tokens(self) -> None:
        self.assertEqual(
            [state.value for state in ApprovalState],
            ["required", "approved", "denied", "not_required"],
        )
        field = approval_state_property()
        self.assertEqual(field["enum"], ["required", "approved", "denied", "not_required"])
        self.assertNotIn("granted", field["enum"])
        self.assertNotIn("not-required", field["enum"])

    def test_instruction_keeps_request_without_decision_in_required_state(self) -> None:
        instruction = approval_result_instruction()
        self.assertIn("no recorded approval decision", instruction)
        self.assertIn("does not become denied", instruction)
        self.assertIn("explicit refusal", instruction)

    def test_wire_schema_matches_the_typed_runtime_contract(self) -> None:
        path = (
            Path(__file__).resolve().parents[2]
            / "ik_extensions/persona_orchestration/contracts/approval-result-v1.json"
        )
        document = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(document["required"], ["schema_version", "approval_state", "executed"])
        self.assertEqual(
            document["properties"]["approval_state"]["enum"],
            ["required", "approved", "denied", "not_required"],
        )
        self.assertFalse(document["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
