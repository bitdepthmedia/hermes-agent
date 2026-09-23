from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.network_guard import (
    MACOS_DENY_NETWORK_POLICY,
    MacOSNetworkIsolation,
    validate_network_proof,
)


@unittest.skipUnless(sys.platform == "darwin", "macOS sandbox proof")
class MacOSNetworkIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.now = datetime(2026, 8, 21, 16, 0, tzinfo=timezone.utc)
        self.adapter = MacOSNetworkIsolation(runtime=Path(sys.executable))

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _proof(self) -> tuple[Path, dict]:
        path = self.root / "network-proof.json"
        outer_proof = os.environ.get("IK_NETWORK_PROOF_PATH")
        if outer_proof:
            receipt = json.loads(Path(outer_proof).read_text(encoding="utf-8"))
            self.now = datetime.fromisoformat(receipt["observed_at"])
            path.write_text(json.dumps(receipt), encoding="utf-8")
        else:
            receipt = self.adapter.create_proof(path, observed_at=self.now, ttl_seconds=300)
        return path, receipt

    def test_loopback_control_succeeds_and_sandboxed_connection_is_denied(self) -> None:
        path, receipt = self._proof()

        self.assertEqual(receipt["status"], "CLEAR")
        self.assertTrue(receipt["probe"]["control_connected"])
        self.assertTrue(receipt["probe"]["sandbox_blocked"])
        self.assertIn(receipt["probe"]["sandbox_errno"], (1, 13))
        self.assertEqual(validate_network_proof(path, runtime=Path(sys.executable), now=self.now), receipt)

    def test_missing_stale_or_tampered_proof_refuses_execution(self) -> None:
        missing = self.root / "missing.json"
        with self.assertRaises(LifecycleBlockedError) as error:
            validate_network_proof(missing, runtime=Path(sys.executable), now=self.now)
        self.assertEqual(error.exception.code, "network_proof_missing")

        path, receipt = self._proof()
        with self.assertRaises(LifecycleBlockedError) as error:
            validate_network_proof(path, runtime=Path(sys.executable), now=self.now + timedelta(seconds=301))
        self.assertEqual(error.exception.code, "network_proof_stale")

        document = deepcopy(receipt)
        document["probe"]["sandbox_blocked"] = False
        path.chmod(0o600)
        path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(LifecycleBlockedError) as error:
            validate_network_proof(path, runtime=Path(sys.executable), now=self.now)
        self.assertEqual(error.exception.code, "network_proof_digest_invalid")

    def test_runtime_policy_or_adapter_drift_fails_closed(self) -> None:
        path, receipt = self._proof()
        other_runtime = self.root / "python"
        other_runtime.write_bytes(Path(sys.executable).read_bytes() + b"drift")
        other_runtime.chmod(0o755)
        with self.assertRaises(LifecycleBlockedError) as error:
            validate_network_proof(path, runtime=other_runtime, now=self.now)
        self.assertEqual(error.exception.code, "network_proof_runtime_drift")

        for field in ("policy_sha256", "adapter_sha256"):
            document = deepcopy(receipt)
            document["bindings"][field] = "f" * 64
            from ik_lifecycle.network_guard import bind_network_proof

            path.chmod(0o600)
            path.write_text(json.dumps(bind_network_proof(document)), encoding="utf-8")
            with self.assertRaises(LifecycleBlockedError) as error:
                validate_network_proof(path, runtime=Path(sys.executable), now=self.now)
            self.assertEqual(error.exception.code, f"network_proof_{field.removesuffix('_sha256')}_drift")

    def test_safe_fixture_runs_only_through_fresh_proven_sandbox(self) -> None:
        path, _ = self._proof()

        result = self.adapter.run(("/usr/bin/true",), proof_path=path, now=self.now)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.argv[0], "/usr/bin/sandbox-exec")
        self.assertIn(MACOS_DENY_NETWORK_POLICY, result.argv)

    def test_unavailable_mechanism_fails_closed(self) -> None:
        adapter = MacOSNetworkIsolation(runtime=Path(sys.executable), sandbox_exec=self.root / "absent")

        with self.assertRaises(LifecycleBlockedError) as error:
            adapter.create_proof(self.root / "proof.json", observed_at=self.now)

        self.assertEqual(error.exception.code, "network_isolation_unavailable")


if __name__ == "__main__":
    unittest.main()
