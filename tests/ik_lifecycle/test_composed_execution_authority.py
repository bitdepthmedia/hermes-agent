from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest

from ik_lifecycle.composed_source import tree_digest
from ik_lifecycle.execution_plan import (
    bind_execution_approval,
    bind_execution_plan,
    run_approved_plan_command,
    validate_composed_execution_plan,
    validate_execution_authorization,
)
from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.network_guard import MACOS_DENY_NETWORK_POLICY, MacOSNetworkIsolation


@unittest.skipUnless(sys.platform == "darwin", "macOS authorization proof")
class ComposedExecutionAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.immutable = self.root / "immutable"
        self.build = self.root / "build"
        self.immutable.mkdir(); self.build.mkdir()
        for target in (self.immutable, self.build):
            (target / "code.py").write_text("composed\n", encoding="utf-8")
        self.tree_sha = tree_digest(self.immutable)
        self.composition_manifest = self.root / "composition.json"
        self.composition_manifest.write_text(json.dumps({
            "composition_id": "composed-1",
            "composed_tree_sha256": self.tree_sha,
            "build_root_pristine_sha256": self.tree_sha,
        }), encoding="utf-8")
        self.now = datetime(2026, 8, 21, 17, 0, tzinfo=timezone.utc)
        self.proof_path = self.root / "network-proof.json"
        self.adapter = MacOSNetworkIsolation(Path(sys.executable))
        self.proof = self.adapter.create_proof(self.proof_path, observed_at=self.now)
        self.plan = self._plan()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _plan(self) -> dict:
        bindings = self.proof["bindings"]
        return bind_execution_plan({
            "schema_id": "ik.hermes.composed-execution-plan.v2",
            "status": "APPROVAL_REQUIRED",
            "implementation_commit": "ec392f4a34b88585d2f874f47d616f32cde20520",
            "candidate": {
                "candidate_id": "1e79082041b08781ca40",
                "target_tag": "v2026.8.18",
                "target_commit_sha": "e624e9fde561e1add9388384012b295fde669ade",
                "source_tree_sha256": "d" * 64,
                "immutable_source": str(self.immutable),
                "execution_root": str(self.root),
            },
            "composition": {
                "composition_id": "composed-1",
                "manifest_path": str(self.composition_manifest),
                "immutable_source": str(self.immutable),
                "build_root": str(self.build),
                "composed_tree_sha256": self.tree_sha,
                "build_root_pristine_sha256": self.tree_sha,
            },
            "network_isolation": {
                "adapter_id": self.proof["adapter_id"],
                "adapter_sha256": bindings["adapter_sha256"],
                "policy_sha256": bindings["policy_sha256"],
                "sandbox_exec_sha256": bindings["sandbox_exec_sha256"],
                "runtime_sha256": bindings["runtime_sha256"],
                "runtime": sys.executable,
                "policy": MACOS_DENY_NETWORK_POLICY,
                "proof_ttl_seconds": 300,
            },
            "authorization": {"executable": False, "separate_approval_required": True},
            "blockers": [],
            "protected_paths": [str(self.root / "running")],
            "batches": [{
                "batch_id": "safe-fixture",
                "classification": "required_candidate_test",
                "status": "APPROVAL_REQUIRED",
                "commands": [{
                    "command_id": "fixture",
                    "status": "PLANNED",
                    "argv": ["/usr/bin/true"],
                    "environment_mode": "replace",
                    "env": {"PATH": "/usr/bin:/bin", "LANG": "C", "TZ": "UTC"},
                    "workdir": str(self.build),
                    "timeout_seconds": 30,
                    "network": "denied",
                    "mutates": [],
                    "expected_artifacts": [],
                    "failure_retention": "retain_outputs_and_logs",
                }],
            }],
        })

    def _approval(self, plan: dict | None = None) -> dict:
        selected = plan or self.plan
        return bind_execution_approval({
            "schema_id": "ik.hermes.execution-approval.v1",
            "status": "APPROVED",
            "approved_at": self.now.isoformat(),
            "expires_at": (self.now + timedelta(minutes=10)).isoformat(),
            "plan_sha256": selected["plan_sha256"],
            "commands_sha256": selected["commands_sha256"],
            "command_digests": [selected["batches"][0]["commands"][0]["command_sha256"]],
            "scope": "exact_composed_candidate_batch",
        })

    def test_old_upstream_only_plan_cannot_authorize_composed_execution(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        old = json.loads((repo / "docs/planning-receipts/2026-08-21-hermes-candidate-execution-plan.json").read_text())

        with self.assertRaises(LifecycleBlockedError) as error:
            validate_composed_execution_plan(old)

        self.assertEqual(error.exception.code, "composed_execution_plan_schema_invalid")

    def test_composition_drift_fails_closed(self) -> None:
        (self.build / "code.py").write_text("drift\n", encoding="utf-8")

        with self.assertRaises(LifecycleBlockedError) as error:
            validate_composed_execution_plan(self.plan)

        self.assertEqual(error.exception.code, "composed_execution_plan_tree_drift")

    def test_no_command_is_authorized_without_separate_exact_approval(self) -> None:
        validate_composed_execution_plan(self.plan)

        with self.assertRaises(LifecycleBlockedError) as error:
            validate_execution_authorization(self.plan, None, proof_path=self.proof_path, now=self.now)

        self.assertEqual(error.exception.code, "execution_approval_missing")

    def test_network_proof_drift_fails_closed(self) -> None:
        from ik_lifecycle.network_guard import bind_network_proof

        proof = deepcopy(self.proof)
        proof["adapter_id"] = "different-adapter"
        self.proof_path.chmod(0o600)
        self.proof_path.write_text(json.dumps(bind_network_proof(proof)), encoding="utf-8")

        with self.assertRaises(LifecycleBlockedError) as error:
            validate_execution_authorization(self.plan, self._approval(), proof_path=self.proof_path, now=self.now)

        self.assertEqual(error.exception.code, "execution_network_proof_mismatch")

    def test_exact_plan_command_and_fresh_proof_return_nonexecuting_authorization(self) -> None:
        result = validate_execution_authorization(
            self.plan,
            self._approval(),
            proof_path=self.proof_path,
            now=self.now,
        )

        self.assertTrue(result["authorized"])
        self.assertEqual(result["plan_sha256"], self.plan["plan_sha256"])
        self.assertEqual(result["command_digests"], self._approval()["command_digests"])

        executed = run_approved_plan_command(
            self.plan,
            self._approval(),
            command_id="fixture",
            proof_path=self.proof_path,
            now=self.now,
        )
        self.assertEqual(executed.returncode, 0)
        self.assertEqual(executed.argv[0], "/usr/bin/sandbox-exec")


if __name__ == "__main__":
    unittest.main()
