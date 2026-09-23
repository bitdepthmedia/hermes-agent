from __future__ import annotations

import json
import unittest

from ik_extensions.persona_orchestration.envelope import Owner
from ik_extensions.persona_orchestration.privacy import LocalTask, PrivacyPolicy, sanitize_for_recipient


class PrivacyTests(unittest.TestCase):
    def test_allowlist_sanitizer_keeps_private_canary_out_of_payload_and_receipt(self) -> None:
        canary = "SYNTHETIC_PRIVATE_CANARY_42"
        task = LocalTask("t1", {"request_type": "research", "topic": "weather", "name": canary, "path": f"/private/{canary}"})
        sanitized = sanitize_for_recipient(task, Owner.BERT, PrivacyPolicy(("request_type", "topic"), "v1"))
        rendered = json.dumps({"payload": sanitized.payload, "receipt": sanitized.receipt})
        self.assertNotIn(canary, rendered)
        self.assertEqual(sanitized.receipt["removed_field_count"], 2)
        self.assertTrue(sanitized.local_mapping_id.startswith("ernie-local:"))

        unsafe_allowed = LocalTask("t2", {"request_type": "research", "topic": canary})
        with self.assertRaisesRegex(ValueError, "sensitive"):
            sanitize_for_recipient(unsafe_allowed, Owner.BERT, PrivacyPolicy(("request_type", "topic"), "v1"))


if __name__ == "__main__":
    unittest.main()
