from __future__ import annotations

import unittest

from ik_extensions.persona_orchestration.learning import LearningPolicy, SafeReceipt, detect_candidate, promotion_decision


class LearningTests(unittest.TestCase):
    def test_three_independent_successes_trigger_but_retries_do_not(self) -> None:
        receipts = [SafeReceipt(f"t{i}", f"p{i}", "same-pattern", True, False) for i in range(3)]
        candidate = detect_candidate(receipts, LearningPolicy())
        self.assertIsNotNone(candidate)
        retries = [SafeReceipt(f"t{i}", "same-parent", "same-pattern", True, True) for i in range(3)]
        self.assertIsNone(detect_candidate(retries, LearningPolicy()))

    def test_only_validated_read_only_local_candidate_auto_enables(self) -> None:
        receipts = [SafeReceipt(f"t{i}", f"p{i}", "same-pattern", True, False) for i in range(3)]
        candidate = detect_candidate(receipts, LearningPolicy())
        self.assertEqual(promotion_decision(candidate, validated=True, effects=frozenset()).status, "auto-enabled")
        for effect in ("dependency", "permission", "schedule", "write", "external", "cloud", "privacy", "authority"):
            self.assertEqual(promotion_decision(candidate, validated=True, effects=frozenset({effect})).status, "approval_required")


if __name__ == "__main__":
    unittest.main()
