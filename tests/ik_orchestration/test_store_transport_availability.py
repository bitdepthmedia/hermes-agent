from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from ik_extensions.persona_orchestration.availability import AvailabilityConfig, run_availability_tick
from ik_extensions.persona_orchestration.envelope import Owner, validate_envelope
from ik_extensions.persona_orchestration.store import HandoffStore
from ik_extensions.persona_orchestration.transport import LoopbackTransport
from tests.ik_orchestration._fixtures import envelope_value


class StoreTransportAvailabilityTests(unittest.TestCase):
    def test_enqueue_is_idempotent_conflicts_block_and_ack_is_monotonic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = HandoffStore(Path(directory) / "handoff.sqlite")
            envelope = validate_envelope(envelope_value())
            first = store.enqueue_once(envelope)
            self.assertEqual(store.enqueue_once(envelope).row_id, first.row_id)
            changed = envelope_value(payload={"request": "changed"})
            with self.assertRaisesRegex(ValueError, "conflict"):
                store.enqueue_once(validate_envelope(changed))
            claimed = store.claim(envelope.task_id, first.version, Owner.ERNIE)
            self.assertEqual(claimed.status, "claimed")
            with self.assertRaisesRegex(ValueError, "CAS"):
                store.claim(envelope.task_id, first.version, Owner.ERNIE)

    def test_offline_ernie_is_one_pending_quiet_retry_then_exactly_once_ack(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = HandoffStore(Path(directory) / "handoff.sqlite")
            value = envelope_value(owner="ernie", privacy_class="local-private", payload={"request": "local action"})
            envelope = validate_envelope(value)
            store.enqueue_once(envelope)
            now = datetime(2026, 8, 21, 23, 0, tzinfo=timezone.utc)
            offline = LoopbackTransport(available=False, authenticated_sender=Owner.BERT, recipient=Owner.ERNIE)
            receipt = run_availability_tick(now, AvailabilityConfig(), store, offline)
            self.assertEqual((receipt.status, receipt.delivered, receipt.notifications), ("QUIET_RETRY", 0, 0))
            self.assertEqual(store.count_pending(), 1)
            due = store.next_attempt_at(envelope.task_id)
            self.assertGreaterEqual((due - now).total_seconds(), 20 * 60)
            self.assertLessEqual((due - now).total_seconds(), 30 * 60)
            online = LoopbackTransport(available=True, authenticated_sender=Owner.BERT, recipient=Owner.ERNIE)
            receipt = run_availability_tick(due, AvailabilityConfig(), store, online)
            self.assertEqual(receipt.delivered, 1)
            self.assertEqual(store.count_pending(), 0)
            self.assertEqual(run_availability_tick(due, AvailabilityConfig(), store, online).delivered, 0)


if __name__ == "__main__":
    unittest.main()
