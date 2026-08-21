from __future__ import annotations

import unittest

from ik_extensions.persona_orchestration.nate_os import MemoryAction, authorize_memory_action


class NateOSBoundaryTests(unittest.TestCase):
    def test_bert_is_all_agents_read_only_and_ernie_can_only_propose(self) -> None:
        self.assertTrue(authorize_memory_action("bert", MemoryAction.READ, "all-agents").allowed)
        self.assertFalse(authorize_memory_action("bert", MemoryAction.WRITE, "all-agents").allowed)
        self.assertTrue(authorize_memory_action("ernie", MemoryAction.PROPOSE, "ernie-local").allowed)
        self.assertFalse(authorize_memory_action("ernie", MemoryAction.WRITE, "canonical").allowed)
        self.assertFalse(authorize_memory_action("unknown", MemoryAction.READ, "ernie-local").allowed)


if __name__ == "__main__":
    unittest.main()
