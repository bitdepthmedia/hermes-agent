from __future__ import annotations

import unittest

from ik_extensions.model_workers.capabilities import ModelCapability
from ik_extensions.model_workers.history import normalize_tool_history
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

    def test_primary_artifact_requires_official_complete_provenance(self) -> None:
        official = ArtifactManifest("Qwen/Qwen3.8-27B", "1" * 40, "apache-2.0", "c" * 64, "a" * 64, "Q4_K_M", "llama.cpp@pinned", 17_000_000_000, "f" * 64, True)
        self.assertEqual(verify_artifact_provenance(official).status, "CLEAR")
        derivative = ArtifactManifest("third-party/modified", "r" * 40, "other", "c" * 64, "q" * 64, "Q4", "runtime", 1, "f" * 64, False)
        self.assertEqual(verify_artifact_provenance(derivative).status, "REJECT_PRIMARY")


if __name__ == "__main__":
    unittest.main()
