from __future__ import annotations

import unittest

from ik_extensions.persona_orchestration.reintegrate import LocalMappingStore, TaskResult, reintegrate_local


class ReintegrationTests(unittest.TestCase):
    def test_result_must_bind_sanitized_task_and_cannot_request_mapping(self) -> None:
        store = LocalMappingStore()
        store.put("ernie-local:t1", {"person": "Synthetic Person"})
        good = TaskResult("t1", "abc", "ernie", "completed", "ik.result.v1", {"summary": "done"})
        self.assertEqual(reintegrate_local(good, store, expected_payload_digest="abc", mapping_id="ernie-local:t1").local_mapping["person"], "Synthetic Person")
        bad = TaskResult("t1", "abc", "ernie", "completed", "ik.result.v1", {"request_local_mapping": True})
        with self.assertRaisesRegex(ValueError, "mapping"):
            reintegrate_local(bad, store, expected_payload_digest="abc", mapping_id="ernie-local:t1")


if __name__ == "__main__":
    unittest.main()
