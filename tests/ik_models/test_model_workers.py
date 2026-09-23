from __future__ import annotations

import json
import unittest

from ik_extensions.model_workers.capabilities import ModelCapability
from ik_extensions.model_workers.history import normalize_tool_history
from ik_extensions.model_workers.qwen38_adapter import adapt_qwen38_messages, qwen38_response_schema
from ik_extensions.persona_orchestration.approval_result import (
    approval_result_instruction,
    approval_state_property,
)
from ik_extensions.model_workers.provenance import ArtifactManifest, verify_artifact_provenance
from ik_extensions.model_workers.router import ModelCatalog, TaskRequirements, select_worker


class ModelWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generalist = ModelCapability("generalist", "fixture", True, True, False, True, 32768, 1, "a" * 64)
        self.coder = ModelCapability("coder", "fixture", True, False, False, True, 32768, 1, "b" * 64)

    def test_conversation_model_is_stable_and_tools_do_not_force_gemma(self) -> None:
        catalog = ModelCatalog(self.generalist, {"coding": self.coder})
        first = select_worker(TaskRequirements("conversation", True, False, None), catalog)
        pinned = select_worker(TaskRequirements("conversation", True, False, first.model.model_id), catalog)
        self.assertEqual(first.model.model_id, "generalist")
        self.assertEqual(pinned.model.model_id, "generalist")
        bounded = select_worker(TaskRequirements("coding", True, True, None), catalog)
        self.assertEqual(bounded.model.model_id, "coder")

    def test_qwen_tool_history_mapping_and_parallel_calls_are_normalized(self) -> None:
        messages = ({"role": "assistant", "reasoning": "kept", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "one", "arguments": {"x": 1}}},
            {"id": "b", "type": "function", "function": {"name": "two", "arguments": "{\"y\":2}"}},
        ]}, {"role": "tool", "tool_call_id": "a", "content": "ok"})
        normalized = normalize_tool_history(messages)
        self.assertEqual(normalized[0]["tool_calls"][0]["function"]["arguments"], "{\"x\":1}")
        self.assertEqual(normalized[0]["tool_calls"][1]["function"]["arguments"], "{\"y\":2}")
        self.assertEqual(normalized[0]["reasoning"], "kept")

    def test_qwen38_history_preserves_mapping_required_by_official_template(self) -> None:
        messages = ({"role": "assistant", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "one", "arguments": "{\"x\":1}"}},
        ]}, {"role": "tool", "tool_call_id": "a", "content": "ok"})
        normalized = normalize_tool_history(messages, dialect="qwen3.8")
        self.assertEqual(normalized[0]["tool_calls"][0]["function"]["arguments"], {"x": 1})

    def test_qwen38_adapter_applies_machine_contract_without_embedding_answers(self) -> None:
        messages = (
            {"role": "system", "content": "Apply the routing policy."},
            {"role": "user", "content": "Return owner and duplicate_execution."},
        )
        adapted = adapt_qwen38_messages(messages, reasoning_enabled=False)
        self.assertIn("requested field names", adapted[0]["content"])
        self.assertIn("lowercase", adapted[0]["content"])
        self.assertNotIn('"codex"', adapted[0]["content"])
        self.assertEqual(adapted[1], messages[1])

    def test_qwen38_result_schema_binds_types_and_global_enums_not_expected_values(self) -> None:
        schema = qwen38_response_schema({"owner": "codex", "result": 323, "executed": False})
        self.assertEqual(schema["required"], ["executed", "owner", "result"])
        self.assertEqual(schema["properties"]["result"], {"type": "integer"})
        self.assertEqual(schema["properties"]["owner"]["enum"], ["bert", "ernie", "codex"])
        serialized = json.dumps(schema, sort_keys=True)
        self.assertNotIn('"const"', serialized)
        self.assertNotIn("323", serialized)

    def test_qwen38_adapter_distinguishes_missing_approval_from_explicit_denial(self) -> None:
        adapted = adapt_qwen38_messages(
            ({"role": "user", "content": "Perform an approval-gated action."},),
            reasoning_enabled=False,
        )
        contract = adapted[0]["content"]
        self.assertIn(approval_result_instruction(), contract)

    def test_qwen38_approval_schema_describes_each_policy_state_without_narrowing_it(self) -> None:
        schema = qwen38_response_schema({"approval_state": "required", "executed": False})
        field = schema["properties"]["approval_state"]
        self.assertEqual(field, approval_state_property())
        self.assertEqual(field["enum"], ["required", "approved", "denied", "not_required"])

    def test_primary_artifact_requires_official_complete_provenance(self) -> None:
        official = ArtifactManifest("Qwen/Qwen3.8-27B", "1" * 40, "apache-2.0", "c" * 64, "a" * 64, "Q4_K_M", "llama.cpp@pinned", 17_000_000_000, "f" * 64, True)
        self.assertEqual(verify_artifact_provenance(official).status, "CLEAR")
        derivative = ArtifactManifest("third-party/modified", "r" * 40, "other", "c" * 64, "q" * 64, "Q4", "runtime", 1, "f" * 64, False)
        self.assertEqual(verify_artifact_provenance(derivative).status, "REJECT_PRIMARY")


if __name__ == "__main__":
    unittest.main()
