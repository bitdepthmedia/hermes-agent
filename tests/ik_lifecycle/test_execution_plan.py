from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unittest

from ik_lifecycle.execution_plan import bind_execution_plan, validate_execution_plan
from ik_lifecycle.models import LifecycleBlockedError


TARGET_SHA = "e624e9fde561e1add9388384012b295fde669ade"
TREE_SHA = "d146de1593a89f1fb9b71c1dc98bd77c554c5d589435f97023635b3da0907b83"


def _plan(tmp_path: Path) -> dict:
    execution_root = tmp_path / "audit"
    source_root = tmp_path / "immutable-source"
    execution_root.mkdir(exist_ok=True)
    source_root.mkdir(exist_ok=True)
    plan = {
        "schema_id": "ik.hermes.candidate-execution-plan.v1",
        "status": "BLOCKED_PREREQUISITES",
        "candidate": {
            "candidate_id": "1e79082041b08781ca40",
            "target_tag": "v2026.8.18",
            "target_commit_sha": TARGET_SHA,
            "source_tree_sha256": TREE_SHA,
            "immutable_source": str(source_root),
            "execution_root": str(execution_root),
        },
        "bindings": {
            "hook_inventory_sha256": "1" * 64,
            "replay_manifest_sha256": "2" * 64,
            "dependency_result_sha256": "3" * 64,
        },
        "authorization": {"executable": False, "approval_scope": "none"},
        "blockers": ["customization_overlay_missing"],
        "protected_paths": [str(tmp_path / "running")],
        "batches": [
            {
                "batch_id": "web-assets",
                "classification": "required_candidate_runtime",
                "status": "BLOCKED_PREREQUISITES",
                "commands": [
                    {
                        "command_id": "web-build",
                        "status": "PLANNED",
                        "argv": ["/pinned/npm", "run", "build", "--workspace", "web", "--ignore-scripts"],
                        "environment_mode": "replace",
                        "env": {
                            "HOME": str(execution_root / "home"),
                            "PATH": "/pinned:/usr/bin:/bin",
                            "LANG": "C.UTF-8",
                            "TZ": "UTC",
                            "CI": "1",
                            "NPM_CONFIG_OFFLINE": "true",
                            "NPM_CONFIG_IGNORE_SCRIPTS": "true",
                            "NPM_CONFIG_AUDIT": "false",
                            "NPM_CONFIG_FUND": "false",
                            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
                            "NPM_CONFIG_CACHE": str(execution_root / "cache"),
                            "NPM_CONFIG_USERCONFIG": str(execution_root / "user.npmrc"),
                            "NPM_CONFIG_GLOBALCONFIG": str(execution_root / "global.npmrc"),
                        },
                        "workdir": str(execution_root),
                        "timeout_seconds": 900,
                        "network": "denied",
                        "mutates": ["hermes_cli/web_dist"],
                        "expected_artifacts": ["hermes_cli/web_dist/index.html"],
                        "failure_retention": "retain_outputs_and_logs",
                    }
                ],
            }
        ],
    }
    return bind_execution_plan(plan)


class ExecutionPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        from tempfile import TemporaryDirectory

        self._temporary = TemporaryDirectory()
        self.tmp_path = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_valid_blocked_plan_binds_commands_and_plan(self) -> None:
        plan = _plan(self.tmp_path)

        result = validate_execution_plan(
            plan,
            candidate_id="1e79082041b08781ca40",
            target_commit_sha=TARGET_SHA,
            source_tree_sha256=TREE_SHA,
        )

        self.assertEqual(result["plan_sha256"], plan["plan_sha256"])
        self.assertEqual(result["command_count"], 1)
        self.assertFalse(result["executable"])


    def test_tampered_command_fails_closed(self) -> None:
        plan = _plan(self.tmp_path)
        plan["batches"][0]["commands"][0]["argv"].append("--watch")

        with self.assertRaises(LifecycleBlockedError) as error:
            validate_execution_plan(plan)

        self.assertEqual(error.exception.code, "execution_plan_digest_invalid")


    def test_blocked_plan_cannot_claim_execution_authority(self) -> None:
        plan = _plan(self.tmp_path)
        plan["authorization"]["executable"] = True
        plan = bind_execution_plan(plan)

        with self.assertRaises(LifecycleBlockedError) as error:
            validate_execution_plan(plan)

        self.assertEqual(error.exception.code, "execution_plan_authority_invalid")


    def test_command_may_not_mutate_immutable_or_protected_roots(self) -> None:
        for unsafe_root in (self.tmp_path / "immutable-source", self.tmp_path / "running"):
            plan = _plan(self.tmp_path)
            plan["batches"][0]["commands"][0]["workdir"] = str(unsafe_root)
            plan = bind_execution_plan(plan)

            with self.assertRaises(LifecycleBlockedError) as error:
                validate_execution_plan(plan)

            self.assertEqual(error.exception.code, "execution_plan_path_invalid")


    def test_executable_command_rejects_forbidden_or_floating_tokens(self) -> None:
        for token in ("axios@1.14.1", "axios@0.30.4", "plain-crypto-js@4.2.1", "@latest", "latest"):
            plan = _plan(self.tmp_path)
            plan["batches"][0]["commands"][0]["argv"].append(token)
            plan = bind_execution_plan(plan)

            with self.assertRaises(LifecycleBlockedError) as error:
                validate_execution_plan(plan)

            self.assertEqual(error.exception.code, "execution_plan_supply_chain_invalid")


    def test_candidate_identity_mismatch_fails_closed(self) -> None:
        plan = _plan(self.tmp_path)

        with self.assertRaises(LifecycleBlockedError) as error:
            validate_execution_plan(plan, target_commit_sha="f" * 40)

        self.assertEqual(error.exception.code, "execution_plan_candidate_mismatch")


    def test_duplicate_command_ids_are_rejected(self) -> None:
        plan = _plan(self.tmp_path)
        duplicate = deepcopy(plan["batches"][0]["commands"][0])
        plan["batches"][0]["commands"].append(duplicate)
        plan = bind_execution_plan(plan)

        with self.assertRaises(LifecycleBlockedError) as error:
            validate_execution_plan(plan)

        self.assertEqual(error.exception.code, "execution_plan_command_invalid")

    def test_npm_command_requires_clean_explicit_config_environment(self) -> None:
        plan = _plan(self.tmp_path)
        command = plan["batches"][0]["commands"][0]
        command["argv"][0] = "/pinned/npm"
        command["env"].pop("NPM_CONFIG_OFFLINE")
        plan = bind_execution_plan(plan)

        with self.assertRaises(LifecycleBlockedError) as error:
            validate_execution_plan(plan)

        self.assertEqual(error.exception.code, "execution_plan_environment_invalid")


    def test_committed_candidate_execution_plan_is_valid_and_non_executable(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        path = repo / "docs/planning-receipts/2026-08-21-hermes-candidate-execution-plan.json"
        plan = json.loads(path.read_text(encoding="utf-8"))

        result = validate_execution_plan(
            plan,
            candidate_id="1e79082041b08781ca40",
            target_commit_sha=TARGET_SHA,
            source_tree_sha256=TREE_SHA,
        )

        self.assertEqual(result, {
            "plan_sha256": "2b656af94ad6d2bdfe866143f05b5432876bbab9848bfda8c50cf240ea285ab5",
            "commands_sha256": "bd9253cf6b332aff96c95d46019e1de7c35de104097980bbf03abb7686bcf224",
            "command_count": 10,
            "executable": False,
        })


    def test_committed_next_approval_input_self_digest_is_valid(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        path = repo / "docs/planning-receipts/2026-08-21-hermes-candidate-next-approval-input.json"
        approval = json.loads(path.read_text(encoding="utf-8"))
        claimed = approval.pop("approval_input_sha256")
        payload = json.dumps(approval, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        self.assertEqual(hashlib.sha256(payload).hexdigest(), claimed)
        self.assertEqual(approval["status"], "DECISION_REQUIRED")
        self.assertIn("any dependency or package-manager command", approval["not_authorized"])

    def test_committed_composed_plan_and_decision_input_are_digest_bound_and_non_executable(self) -> None:
        repo = Path(__file__).resolve().parents[2]
        plan = json.loads((repo / "docs/planning-receipts/2026-08-21-hermes-composed-execution-plan-v2.json").read_text())
        claimed_plan = plan["plan_sha256"]
        unsigned_plan = {key: value for key, value in plan.items() if key != "plan_sha256"}
        self.assertEqual(
            hashlib.sha256(json.dumps(unsigned_plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
            claimed_plan,
        )
        self.assertEqual(plan["schema_id"], "ik.hermes.composed-execution-plan.v2")
        self.assertFalse(plan["authorization"]["executable"])
        self.assertEqual(plan["command_count"], 7)

        approval = json.loads((repo / "docs/planning-receipts/2026-08-21-hermes-composed-execution-approval-input.json").read_text())
        claimed_approval = approval.pop("approval_input_sha256")
        self.assertEqual(
            hashlib.sha256(json.dumps(approval, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
            claimed_approval,
        )
        self.assertEqual(approval["status"], "DECISION_REQUIRED")
        self.assertEqual(approval["plan_sha256"], claimed_plan)


if __name__ == "__main__":
    unittest.main()
