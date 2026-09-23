from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from ik_extensions.model_workers.offline_eval import (
    OfflineEvalError,
    build_request_payload,
    grade_case,
    load_runtime_cases,
    run_concurrency_probe,
    run_runtime_cases,
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

        def fake_run(endpoint, model, cases, *, timeout_seconds, authorization_bearer=None):
            barrier.wait(timeout=2)
            return ({"case_id": cases[0].case_id, "passed": True, "latency_ms": 7, "error_code": None},)

        with patch("ik_extensions.model_workers.offline_eval.run_runtime_cases", side_effect=fake_run):
            receipt = run_concurrency_probe("http://127.0.0.1:11588", "fixture", timeout_seconds=5)
        self.assertEqual(receipt["status"], "CLEAR")
        self.assertEqual(receipt["requested_concurrency"], 2)
        self.assertEqual(receipt["successful_requests"], 2)

    def test_runtime_transport_uses_opaque_bearer_without_receipt_leak(self) -> None:
        observed: list[str | None] = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - stdlib callback name
                observed.append(self.headers.get("Authorization"))
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                body = json.dumps({"message": {"content": '{"owner":"codex","duplicate_execution":false}'}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            case = next(
                case
                for case in load_runtime_cases(Path(__file__).resolve().parents[2] / "evals/ik")
                if case.case_id == "cos-work-to-codex"
            )
            outcomes = run_runtime_cases(
                f"http://127.0.0.1:{server.server_port}",
                "fixture",
                (case,),
                authorization_bearer="opaque-secret-value",
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
        self.assertEqual(observed, ["Bearer opaque-secret-value"])
        self.assertNotIn("opaque-secret-value", json.dumps(outcomes))

    def test_runner_requires_named_nonempty_environment_handle_without_disclosure(self) -> None:
        runner = Path(__file__).resolve().parents[2] / "scripts/ik-offline-model-eval"
        fixtures = Path(__file__).resolve().parents[2] / "evals/ik"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt.json"
            environment = os.environ.copy()
            environment.pop("IK_TEST_ROUTER_HANDLE", None)
            completed = subprocess.run(
                (
                    sys.executable,
                    str(runner),
                    "--fixtures", str(fixtures),
                    "--endpoint", "http://127.0.0.1:1",
                    "--model", "fixture",
                    "--output", str(output),
                    "--api-key-env", "IK_TEST_ROUTER_HANDLE",
                ),
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("credential handle is unavailable", completed.stderr)
        self.assertNotIn("opaque-secret-value", completed.stdout + completed.stderr)

    def test_concurrency_probe_fails_closed_on_one_transport_error(self) -> None:
        def fake_run(endpoint, model, cases, *, timeout_seconds, authorization_bearer=None):
            code = None if cases[0].case_id.endswith("alpha") else "TimeoutError"
            return ({"case_id": cases[0].case_id, "passed": code is None, "latency_ms": 7, "error_code": code},)

        with patch("ik_extensions.model_workers.offline_eval.run_runtime_cases", side_effect=fake_run):
            receipt = run_concurrency_probe("http://127.0.0.1:11588", "fixture", timeout_seconds=5)
        self.assertEqual(receipt["status"], "BLOCKED")
        self.assertEqual(receipt["successful_requests"], 1)

    def test_qwen38_payload_uses_schema_and_adapter_without_changing_case(self) -> None:
        case = next(
            case
            for case in load_runtime_cases(Path(__file__).resolve().parents[2] / "evals/ik")
            if case.case_id == "tools-history-replay"
        )
        payload = build_request_payload(case, "ik-qwen38-eval:31629f53165a")
        self.assertIsInstance(payload["format"], dict)
        self.assertEqual(payload["format"]["required"], ["paired_results", "result"])
        assistant = next(message for message in payload["messages"] if message.get("role") == "assistant")
        self.assertIsInstance(assistant["tool_calls"][0]["function"]["arguments"], dict)
        self.assertEqual(case.system, "Read the synthetic tool history and return JSON only.")

    def test_non_qwen_payload_retains_generic_json_contract(self) -> None:
        case = next(
            case
            for case in load_runtime_cases(Path(__file__).resolve().parents[2] / "evals/ik")
            if case.case_id == "tools-approval"
        )
        payload = build_request_payload(case, "gemma4:26b")
        self.assertEqual(payload["format"], "json")


if __name__ == "__main__":
    unittest.main()
