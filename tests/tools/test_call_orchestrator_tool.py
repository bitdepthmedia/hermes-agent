import importlib
import hashlib
import json
import os
import unittest
from unittest.mock import patch


def _canonical_bytes(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class CallOrchestratorToolTests(unittest.TestCase):
    def setUp(self):
        self.previous_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.previous_env)

    def test_schema_uses_primary_orchestrator_naming(self):
        tool = importlib.import_module("tools.call_orchestrator_tool")

        self.assertEqual(tool.CALL_ORCHESTRATOR_SCHEMA["name"], "call_orchestrator")
        serialized = str(tool.CALL_ORCHESTRATOR_SCHEMA).lower()
        self.assertNotIn("bert", serialized)

    def test_requirements_use_neutral_environment_names(self):
        tool = importlib.import_module("tools.call_orchestrator_tool")
        os.environ["HERMES_ORCHESTRATOR_API_KEY"] = "test-key"
        os.environ["HERMES_ORCHESTRATOR_BASE_URL"] = "http://127.0.0.1:8643/v1"

        self.assertTrue(tool.check_call_orchestrator_requirements())

    def test_read_only_client_accepts_strict_bound_attestation(self):
        tool = importlib.import_module("tools.call_orchestrator_read_only")
        os.environ["HERMES_ORCHESTRATOR_API_KEY"] = "test-key"
        os.environ["HERMES_ORCHESTRATOR_BASE_URL"] = "http://127.0.0.1:8643/v1"

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data)
            content = '{"status":"NO_PENDING_WORK"}'
            receipts = {
                "purpose": "status",
                "items": [
                    {
                        "kind": "session_db_metadata",
                        "pagination": {"complete": True, "truncated": False},
                    }
                ],
            }
            return _FakeResponse(
                {
                    "success": True,
                    "content": content,
                    "source_receipts": receipts,
                    "attestation": {
                        "mode": "no_tools",
                        "enabled_toolsets": [],
                        "tool_names": [],
                        "tool_calls": 0,
                        "request_sha256": hashlib.sha256(
                            _canonical_bytes(payload)
                        ).hexdigest(),
                        "input_sha256": hashlib.sha256(
                            payload["input"].encode("utf-8")
                        ).hexdigest(),
                        "output_sha256": hashlib.sha256(
                            content.encode("utf-8")
                        ).hexdigest(),
                        "source_receipts_sha256": hashlib.sha256(
                            _canonical_bytes(receipts)
                        ).hexdigest(),
                    },
                }
            )

        with patch(
            "tools.call_orchestrator_read_only.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            result = json.loads(
                tool.call_orchestrator_read_only(
                    input_text="Return strict status JSON.",
                    purpose="status",
                    max_tokens=400,
                )
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["attestation"]["mode"], "no_tools")

    def test_read_only_client_fails_closed_on_tampered_output_digest(self):
        tool = importlib.import_module("tools.call_orchestrator_read_only")
        os.environ["HERMES_ORCHESTRATOR_API_KEY"] = "test-key"
        os.environ["HERMES_ORCHESTRATOR_BASE_URL"] = "http://127.0.0.1:8643/v1"

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data)
            receipts = {"purpose": "status", "items": []}
            return _FakeResponse(
                {
                    "success": True,
                    "content": "tampered",
                    "source_receipts": receipts,
                    "attestation": {
                        "mode": "no_tools",
                        "enabled_toolsets": [],
                        "tool_names": [],
                        "tool_calls": 0,
                        "request_sha256": hashlib.sha256(
                            _canonical_bytes(payload)
                        ).hexdigest(),
                        "input_sha256": hashlib.sha256(
                            payload["input"].encode("utf-8")
                        ).hexdigest(),
                        "output_sha256": "0" * 64,
                        "source_receipts_sha256": hashlib.sha256(
                            _canonical_bytes(receipts)
                        ).hexdigest(),
                    },
                }
            )

        with patch(
            "tools.call_orchestrator_read_only.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            result = json.loads(
                tool.call_orchestrator_read_only(
                    input_text="Return strict status JSON.",
                    purpose="status",
                )
            )

        self.assertFalse(result["success"])
        self.assertIn("attestation", result["error"].lower())

    def test_read_only_client_fails_closed_on_missing_attestation(self):
        tool = importlib.import_module("tools.call_orchestrator_read_only")
        os.environ["HERMES_ORCHESTRATOR_API_KEY"] = "test-key"
        os.environ["HERMES_ORCHESTRATOR_BASE_URL"] = "http://127.0.0.1:8643/v1"

        with patch(
            "tools.call_orchestrator_read_only.urllib.request.urlopen",
            return_value=_FakeResponse(
                {
                    "success": True,
                    "content": "unattested",
                    "source_receipts": {"purpose": "status", "items": []},
                }
            ),
        ):
            result = json.loads(
                tool.call_orchestrator_read_only(
                    input_text="Return strict status JSON.",
                    purpose="status",
                )
            )

        self.assertFalse(result["success"])
        self.assertIn("attestation", result["error"].lower())

    def test_read_only_client_fails_closed_if_attestation_names_tools(self):
        tool = importlib.import_module("tools.call_orchestrator_read_only")
        os.environ["HERMES_ORCHESTRATOR_API_KEY"] = "test-key"
        os.environ["HERMES_ORCHESTRATOR_BASE_URL"] = "http://127.0.0.1:8643/v1"

        def fake_urlopen(request, timeout):
            payload = json.loads(request.data)
            content = "invalid tool claim"
            receipts = {"purpose": "status", "items": []}
            return _FakeResponse(
                {
                    "success": True,
                    "content": content,
                    "source_receipts": receipts,
                    "attestation": {
                        "mode": "no_tools",
                        "enabled_toolsets": ["web"],
                        "tool_names": ["web_search"],
                        "tool_calls": 0,
                        "request_sha256": hashlib.sha256(
                            _canonical_bytes(payload)
                        ).hexdigest(),
                        "input_sha256": hashlib.sha256(
                            payload["input"].encode("utf-8")
                        ).hexdigest(),
                        "output_sha256": hashlib.sha256(
                            content.encode("utf-8")
                        ).hexdigest(),
                        "source_receipts_sha256": hashlib.sha256(
                            _canonical_bytes(receipts)
                        ).hexdigest(),
                    },
                }
            )

        with patch(
            "tools.call_orchestrator_read_only.urllib.request.urlopen",
            side_effect=fake_urlopen,
        ):
            result = json.loads(
                tool.call_orchestrator_read_only(
                    input_text="Return strict status JSON.",
                    purpose="status",
                )
            )

        self.assertFalse(result["success"])
        self.assertIn("attestation", result["error"].lower())

    def test_read_only_client_rejects_non_loopback_or_wrong_port(self):
        tool = importlib.import_module("tools.call_orchestrator_read_only")
        os.environ["HERMES_ORCHESTRATOR_API_KEY"] = "test-key"

        for url in (
            "https://127.0.0.1:8643/v1",
            "http://example.com:8643/v1",
            "http://127.0.0.1:8642/v1",
        ):
            os.environ["HERMES_ORCHESTRATOR_BASE_URL"] = url
            result = json.loads(
                tool.call_orchestrator_read_only(
                    input_text="Return strict status JSON.",
                    purpose="status",
                )
            )
            self.assertFalse(result["success"], url)

    def test_read_only_review_requires_matching_bounded_receipt_hash(self):
        tool = importlib.import_module("tools.call_orchestrator_read_only")
        os.environ["HERMES_ORCHESTRATOR_API_KEY"] = "test-key"
        os.environ["HERMES_ORCHESTRATOR_BASE_URL"] = "http://127.0.0.1:8643/v1"

        result = json.loads(
            tool.call_orchestrator_read_only(
                input_text="Review this evidence.",
                purpose="review",
                source_receipt={
                    "content": {"summary": "bounded evidence"},
                    "sha256": "f" * 64,
                },
            )
        )

        self.assertFalse(result["success"])
        self.assertIn("receipt hash", result["error"].lower())


if __name__ == "__main__":
    unittest.main()
