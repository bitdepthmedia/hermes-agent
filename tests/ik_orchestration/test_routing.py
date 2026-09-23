from __future__ import annotations

import unittest

from ik_extensions.persona_orchestration.envelope import Owner
from ik_extensions.persona_orchestration.routing import IntakeRequest, RoutingPolicy, classify_request, decompose_mixed


class RoutingTests(unittest.TestCase):
    def test_work_routes_once_to_codex_and_private_source_gets_projection(self) -> None:
        request = IntakeRequest("r1", Owner.ERNIE, ("work",), True, "build the workflow", "ernie-local:r1")
        decision = classify_request(request, RoutingPolicy())
        self.assertEqual((decision.owner, decision.evidence_code), (Owner.CODEX, "declared-work-owner"))
        self.assertEqual(decision.privacy_class.value, "sanitized-cloud")
        self.assertNotIn("build the workflow", decision.safe_payload["request"])
        self.assertEqual(decision.local_payload_ref, "ernie-local:r1")

        accepted = IntakeRequest("r2", Owner.BERT, ("work",), False, "continue", None, accepted_owner=Owner.CODEX)
        self.assertEqual(classify_request(accepted, RoutingPolicy()).owner, Owner.CODEX)

    def test_personal_stays_with_persona_and_mixed_decomposes(self) -> None:
        personal = classify_request(IntakeRequest("r3", Owner.BERT, ("personal",), False, "schedule", None), RoutingPolicy())
        self.assertEqual(personal.owner, Owner.BERT)
        mixed = classify_request(IntakeRequest("r4", Owner.ERNIE, ("work", "personal"), False, "mixed", None), RoutingPolicy())
        children = decompose_mixed(IntakeRequest("r4", Owner.ERNIE, ("work", "personal"), False, "mixed", None), mixed)
        self.assertEqual({child.owner for child in children}, {Owner.CODEX, Owner.ERNIE})
        self.assertEqual({child.parent_task_id for child in children}, {"r4"})

    def test_ambiguous_request_requires_clarification(self) -> None:
        decision = classify_request(IntakeRequest("r5", Owner.BERT, (), False, "ambiguous", None), RoutingPolicy())
        self.assertTrue(decision.clarification_required)
        self.assertEqual(decision.owner, Owner.BERT)


if __name__ == "__main__":
    unittest.main()
