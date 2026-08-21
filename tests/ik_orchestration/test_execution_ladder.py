from __future__ import annotations

import unittest

from ik_extensions.persona_orchestration.execution import Capability, ExecutionRung, choose_execution_rung


class ExecutionLadderTests(unittest.TestCase):
    def test_cheapest_safe_capability_wins_without_authority_expansion(self) -> None:
        capabilities = (
            Capability("agent", ExecutionRung.SUBAGENT, frozenset({"read"}), False),
            Capability("script", ExecutionRung.WORKFLOW, frozenset({"read"}), False),
        )
        decision = choose_execution_rung(frozenset({"read"}), False, capabilities)
        self.assertEqual((decision.rung, decision.capability_id), (ExecutionRung.WORKFLOW, "script"))
        with self.assertRaisesRegex(ValueError, "authority"):
            choose_execution_rung(frozenset({"write"}), False, capabilities)

    def test_recurring_is_durable_and_background_handle_keeps_persona_available(self) -> None:
        capabilities = (Capability("recurring", ExecutionRung.DURABLE, frozenset({"read"}), True),)
        decision = choose_execution_rung(frozenset({"read"}), True, capabilities)
        self.assertEqual(decision.rung, ExecutionRung.DURABLE)
        self.assertTrue(decision.persona_remains_available)
        self.assertTrue(decision.background_handle.startswith("bg:"))


if __name__ == "__main__":
    unittest.main()
