from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from ik_lifecycle.cells import load_cell_spec
from ik_lifecycle.health import HealthEvidence, verify_cell
from ik_lifecycle.promotion import ApprovalReceipt, PairedPointers, promote_pair
from ik_lifecycle.rollback import RollbackMode, rollback_pair


class CellHealthRollbackTests(unittest.TestCase):
    def test_cell_specs_are_independent_and_bert_is_sanitized_read_only(self) -> None:
        root = Path(__file__).resolve().parents[2]
        ernie = load_cell_spec(root / "ik_cells/ernie.yaml")
        bert = load_cell_spec(root / "ik_cells/bert.yaml")
        self.assertNotEqual(ernie.state_root_key, bert.state_root_key)
        self.assertEqual((ernie.trust_zone, bert.trust_zone), ("local-private", "sanitized-cloud"))
        self.assertFalse(bert.nate_os_write_allowed)
        self.assertIn("computer_history_path_adaptation", ernie.external_approval_gates)
        self.assertIn("computer_history_path_adaptation", bert.external_approval_gates)

    def test_health_is_all_clear_and_automation_blocker_fails_closed(self) -> None:
        clear = HealthEvidence({name: "CLEAR" for name in HealthEvidence.REQUIRED}, "a" * 40, "a" * 40, "PAUSED")
        self.assertEqual(verify_cell(clear).status, "CLEAR")
        blocked = HealthEvidence(clear.gates, "a" * 40, "a" * 40, "ACTIVE")
        self.assertEqual(verify_cell(blocked).status, "BLOCKED")

    def test_pair_promotion_is_atomic_and_post_write_rollback_requires_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pointers = PairedPointers(root / "release.json", root / "profile.json", root / "journal.json")
            pointers.initialize("release-old", "profile-old", 1)
            approval = ApprovalReceipt("ernie", "release-new", datetime.now(timezone.utc) + timedelta(hours=1), "d" * 64)
            receipt = promote_pair(pointers, "release-new", "profile-new", 2, approval, service_closed=True)
            self.assertEqual(pointers.read_pair(), ("release-new", "profile-new", 2))
            restored = rollback_pair(pointers, receipt, RollbackMode.PRE_TRAFFIC, delta_reconciled=False)
            self.assertEqual((restored.status, pointers.read_pair()), ("ROLLED_BACK", ("release-old", "profile-old", 1)))
            receipt = promote_pair(pointers, "release-new", "profile-new", 2, approval, service_closed=True)
            self.assertEqual(rollback_pair(pointers, receipt, RollbackMode.POST_WRITE, delta_reconciled=False).status, "APPROVAL_REQUIRED")

    def test_injected_crash_between_pointer_writes_recovers_previous_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pointers = PairedPointers(root / "release.json", root / "profile.json", root / "journal.json")
            pointers.initialize("release-old", "profile-old", 1)
            with self.assertRaisesRegex(RuntimeError, "injected"):
                pointers.switch("release-new", "profile-new", 2, crash_after_release=True)
            pointers.recover()
            self.assertEqual(pointers.read_pair(), ("release-old", "profile-old", 1))


if __name__ == "__main__":
    unittest.main()
