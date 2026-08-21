from __future__ import annotations

from pathlib import Path
import unittest

from ik_extensions.model_workers.eval_harness import load_cases, score_fixture


class EvalHarnessTests(unittest.TestCase):
    def test_frozen_synthetic_suites_cover_required_worker_boundaries(self) -> None:
        root = Path(__file__).resolve().parents[2] / "evals/ik"
        cases = load_cases(root)
        categories = {case.category for case in cases}
        self.assertTrue({"chief-of-staff", "tools", "coding-reasoning", "long-context", "privacy-handoff"}.issubset(categories))
        result = score_fixture(cases, {case.case_id: "PASS" for case in cases})
        self.assertEqual((result.status, result.pass_rate), ("CLEAR", 1.0))


if __name__ == "__main__":
    unittest.main()
