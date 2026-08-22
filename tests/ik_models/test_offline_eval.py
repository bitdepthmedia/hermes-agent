from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from ik_extensions.model_workers.offline_eval import (
    OfflineEvalError,
    grade_case,
    load_runtime_cases,
    run_concurrency_probe,
    summarize_results,
)


class OfflineEvalTests(unittest.TestCase):
    def test_runtime_cases_are_frozen_non_private_and_cover_all_gates(self) -> None:
        root = Path(__file__).resolve().parents[2] / "evals/ik"
        cases = load_runtime_cases(root)
        categories = {case.category for case in cases}
        self.assertEqual(len(cases), 12)
        self.assertTrue(
            {
                "chief-of-staff",
                "tools",
                "coding-reasoning",
                "long-context",
                "privacy-handoff",
                "multimodal",
            }.issubset(categories)
        )
        serialized = json.dumps([case.__dict__ for case in cases])
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("private clone", serialized.lower())

    def test_json_subset_tool_calls_privacy_and_text_graders(self) -> None:
        cases = {case.case_id: case for case in load_runtime_cases(Path(__file__).resolve().parents[2] / "evals/ik")}
        self.assertTrue(grade_case(cases["cos-work-to-codex"], {"message": {"content": '{"owner":"codex","duplicate_execution":false}'}}))
        self.assertTrue(grade_case(cases["tools-parallel-history"], {"message": {"tool_calls": [{"function": {"name": "lookup_alpha"}}, {"function": {"name": "lookup_beta"}}]}}))
        self.assertTrue(grade_case(cases["privacy-canary-zero-leak"], {"message": {"content": '{"leak_count":0,"executed":false}'}}))
        self.assertFalse(grade_case(cases["privacy-canary-zero-leak"], {"message": {"content": "IK_SYNTHETIC_CANARY_7F3A"}}))
        self.assertTrue(grade_case(cases["vision-synthetic-color"], {"message": {"content": "The square is red."}}))

    def test_receipt_is_aggregate_only_and_critical_failure_blocks(self) -> None:
        cases = load_runtime_cases(Path(__file__).resolve().parents[2] / "evals/ik")
        outcomes = []
        for case in cases:
            outcomes.append({"case_id": case.case_id, "passed": case.case_id != "privacy-canary-zero-leak", "latency_ms": 10, "input_tokens": 3, "output_tokens": 2})
        receipt = summarize_results(cases, outcomes, model_alias="candidate-qwen38")
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertIn("privacy-canary-zero-leak", receipt["failed_critical"])
        self.assertNotIn("response", json.dumps(receipt))

    def test_zero_cases_or_unknown_grader_fail_closed(self) -> None:
        with self.assertRaisesRegex(OfflineEvalError, "no_runtime_cases"):
            summarize_results((), (), model_alias="fixture")

    def test_runner_resolves_repo_imports_outside_repo_workdir(self) -> None:
        runner = Path(__file__).resolve().parents[2] / "scripts/ik-offline-model-eval"
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run(
                (sys.executable, str(runner), "--help"),
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("--fixtures", completed.stdout)
        self.assertIn("--concurrency-probe", completed.stdout)

    def test_concurrency_probe_requires_two_overlapping_transport_successes(self) -> None:
        barrier = threading.Barrier(2)

        def fake_run(endpoint, model, cases, *, timeout_seconds):
            barrier.wait(timeout=2)
            return ({"case_id": cases[0].case_id, "passed": True, "latency_ms": 7, "error_code": None},)

        with patch("ik_extensions.model_workers.offline_eval.run_runtime_cases", side_effect=fake_run):
            receipt = run_concurrency_probe("http://127.0.0.1:11588", "fixture", timeout_seconds=5)
        self.assertEqual(receipt["status"], "CLEAR")
        self.assertEqual(receipt["requested_concurrency"], 2)
        self.assertEqual(receipt["successful_requests"], 2)

    def test_concurrency_probe_fails_closed_on_one_transport_error(self) -> None:
        def fake_run(endpoint, model, cases, *, timeout_seconds):
            code = None if cases[0].case_id.endswith("alpha") else "TimeoutError"
            return ({"case_id": cases[0].case_id, "passed": code is None, "latency_ms": 7, "error_code": code},)

        with patch("ik_extensions.model_workers.offline_eval.run_runtime_cases", side_effect=fake_run):
            receipt = run_concurrency_probe("http://127.0.0.1:11588", "fixture", timeout_seconds=5)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertEqual(receipt["successful_requests"], 1)


if __name__ == "__main__":
    unittest.main()
