from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ik_lifecycle.audited_dependencies import dependency_tree_digest
from ik_lifecycle.correction_contract import (
    bind_correction_contract,
    validate_correction_contract,
)
from ik_lifecycle.execution_plan import (
    bind_execution_approval,
    bind_execution_plan,
    validate_corrected_execution_plan,
    validate_execution_approval_binding,
)
from ik_lifecycle.models import LifecycleBlockedError


class CorrectedExecutionContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.build = self.root / "build"
        self.build.mkdir()
        self.dependencies = self.build / "ui-tui/node_modules"
        self.dependencies.mkdir(parents=True)
        (self.dependencies / "safe.txt").write_text("safe\n", encoding="utf-8")
        self.cache_root = self.root / "disposable-caches"
        self.cache_root.mkdir()
        self.cache = self.cache_root / "ui-tui-vite"
        self.cache.mkdir()
        self.config = self.build / "ik_lifecycle/vite/ui_tui_vitest.config.ts"
        self.config.parent.mkdir(parents=True)
        self.config.write_text("export default {}\n", encoding="utf-8")
        self.contract = bind_correction_contract({
            "schema_id": "ik.hermes.composed-execution-correction.v1",
            "build_root": str(self.build),
            "dependency_surfaces": [{
                "path": str(self.dependencies),
                "sha256": dependency_tree_digest(self.dependencies),
                "retention": "immutable_fail_closed",
            }],
            "disposable_cache_root": str(self.cache_root),
            "caches": [{
                "cache_id": "ui-tui-vite",
                "path": str(self.cache),
                "pre_sha256": dependency_tree_digest(self.cache),
                "retention": "retain_on_failure_discard_after_clear_receipt",
            }],
            "vite_configs": [{
                "config_id": "ui-tui",
                "path": str(self.config),
                "sha256": hashlib.sha256(self.config.read_bytes()).hexdigest(),
                "cache_id": "ui-tui-vite",
            }],
            "prior_failed_plan_sha256": "1" * 64,
            "prior_failed_commands_sha256": "2" * 64,
            "rerun_decision": "rerun_all_commands_in_new_composition",
        })

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_declared_empty_disposable_cache_and_immutable_dependencies_are_clear(self) -> None:
        result = validate_correction_contract(self.contract)
        self.assertEqual(result["status"], "CLEAR")
        self.assertEqual(result["cache_count"], 1)

    def test_undeclared_cache_or_dependency_mutation_fails_closed(self) -> None:
        (self.dependencies / ".vite").mkdir()
        with self.assertRaises(LifecycleBlockedError) as dependency:
            validate_correction_contract(self.contract)
        self.assertEqual(dependency.exception.code, "corrected_dependency_surface_drift")

        (self.dependencies / ".vite").rmdir()
        (self.cache / "unexpected").write_text("cache\n", encoding="utf-8")
        with self.assertRaises(LifecycleBlockedError) as cache:
            validate_correction_contract(self.contract)
        self.assertEqual(cache.exception.code, "corrected_cache_prestate_drift")

    def test_cache_paths_must_be_absolute_confined_and_outside_dependencies(self) -> None:
        escaped = deepcopy(self.contract)
        escaped["caches"][0]["path"] = str(self.root / "escape")
        escaped = bind_correction_contract(escaped)
        with self.assertRaises(LifecycleBlockedError) as outside:
            validate_correction_contract(escaped)
        self.assertEqual(outside.exception.code, "corrected_cache_path_invalid")

        nested = deepcopy(self.contract)
        nested["disposable_cache_root"] = str(self.dependencies)
        nested["caches"][0]["path"] = str(self.dependencies / "cache")
        (self.dependencies / "cache").mkdir()
        nested["caches"][0]["pre_sha256"] = dependency_tree_digest(self.dependencies / "cache")
        nested["dependency_surfaces"][0]["sha256"] = dependency_tree_digest(self.dependencies)
        nested = bind_correction_contract(nested)
        with self.assertRaises(LifecycleBlockedError) as overlap:
            validate_correction_contract(nested)
        self.assertEqual(overlap.exception.code, "corrected_cache_path_invalid")

    def test_corrected_validator_rejects_old_schema_and_old_approval_digest(self) -> None:
        old_plan = bind_execution_plan({
            "schema_id": "ik.hermes.composed-execution-plan.v2",
            "status": "APPROVAL_REQUIRED",
            "authorization": {"executable": False},
            "blockers": [],
            "batches": [],
        })
        with self.assertRaises(LifecycleBlockedError) as error:
            validate_corrected_execution_plan(old_plan)
        self.assertEqual(error.exception.code, "corrected_execution_plan_schema_invalid")

        corrected = {
            "schema_id": "ik.hermes.corrected-composed-execution-plan.v3",
            "plan_sha256": "3" * 64,
            "commands_sha256": "4" * 64,
            "batches": [{"commands": [{"command_sha256": "5" * 64}]}],
        }
        old_approval = bind_execution_approval({
            "schema_id": "ik.hermes.execution-approval.v1",
            "status": "APPROVED",
            "approved_at": "2026-08-21T17:00:00+00:00",
            "expires_at": "2026-08-21T17:10:00+00:00",
            "plan_sha256": "1" * 64,
            "commands_sha256": "2" * 64,
            "command_digests": ["6" * 64],
            "scope": "exact_composed_candidate_batch",
        })
        from datetime import datetime, timezone

        with self.assertRaises(LifecycleBlockedError) as approval:
            validate_execution_approval_binding(
                corrected,
                old_approval,
                now=datetime(2026, 8, 21, 17, 5, tzinfo=timezone.utc),
            )
        self.assertEqual(approval.exception.code, "execution_approval_mismatch")


if __name__ == "__main__":
    unittest.main()
