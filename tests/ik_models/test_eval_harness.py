from __future__ import annotations

from pathlib import Path
import json
import tempfile
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

    def test_explicit_non_model_acceptance_contract_is_ignored_but_unknown_schema_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "model-v1.json").write_text(
                json.dumps({"schema_id": "ik.hermes.model-eval-suite.v1", "cases": [{"case_id": "one", "category": "tools", "critical": True, "expected": {}}]}),
                encoding="utf-8",
            )
            (root / "acceptance-v1.json").write_text(
                json.dumps({"schema_id": "ik.ernie-cell-acceptance.v1", "required_gates": ["health"]}),
                encoding="utf-8",
            )
            self.assertEqual([case.case_id for case in load_cases(root)], ["one"])
            (root / "unknown-v1.json").write_text('{"schema_id":"unknown"}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema mismatch"):
                load_cases(root)


if __name__ == "__main__":
    unittest.main()
