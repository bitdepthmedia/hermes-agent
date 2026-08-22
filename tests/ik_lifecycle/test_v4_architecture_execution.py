from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ik_lifecycle.architecture_coverage import (
    REQUIRED_ARCHITECTURE_INVARIANTS,
    validate_architecture_coverage,
)
from ik_lifecycle.execution_plan import validate_v4_execution_plan
from ik_lifecycle.focused_test_selection import (
    BEHAVIOR_TEST_PATHS,
    discover_focused_tests,
)
from ik_lifecycle.models import LifecycleBlockedError


class V4ArchitectureExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = Path(__file__).resolve().parents[2]
        self.behavior = discover_focused_tests(self.repo, BEHAVIOR_TEST_PATHS, suite_id="behavior")
        self.lifecycle = discover_focused_tests(
            self.repo,
            ("tests/ik_lifecycle/test_cells_health_rollback.py", "tests/ik_lifecycle/test_continuity_migration.py", "tests/ik_lifecycle/test_sealing_split.py"),
            suite_id="lifecycle",
        )
        self.spec = self.repo / "docs/architecture/bert-ernie-hermes-cell-architecture.md"
        all_ids = self.behavior["test_ids"] + self.lifecycle["test_ids"]
        self.mapping = {
            "schema_id": "ik.hermes.architecture-coverage.v1",
            "architecture_spec": {"path": "docs/architecture/bert-ernie-hermes-cell-architecture.md", "sha256": hashlib.sha256(self.spec.read_bytes()).hexdigest()},
            "suites": {
                "ik-orchestration": {"required_paths": [p for p in BEHAVIOR_TEST_PATHS if "/ik_orchestration/" in p]},
                "ik-models": {"required_paths": [p for p in BEHAVIOR_TEST_PATHS if "/ik_models/" in p]},
            },
            "invariants": {name: {"test_ids": [all_ids[index % len(all_ids)]]} for index, name in enumerate(REQUIRED_ARCHITECTURE_INVARIANTS)},
        }

    def test_behavior_discovery_is_exact_nonzero_and_contains_both_suites(self) -> None:
        self.assertEqual(tuple(self.behavior["selected_paths"]), BEHAVIOR_TEST_PATHS)
        self.assertEqual(self.behavior["module_count"], 11)
        self.assertGreater(self.behavior["test_count"], 0)
        self.assertEqual(len(self.behavior["test_ids"]), self.behavior["test_count"])
        self.assertTrue(any("/ik_orchestration/" in path for path in self.behavior["selected_paths"]))
        self.assertTrue(any("/ik_models/" in path for path in self.behavior["selected_paths"]))

    def test_declared_architecture_mapping_covers_every_required_invariant(self) -> None:
        mapping = json.loads((self.repo / "ik_lifecycle/manifests/architecture-coverage-v1.json").read_text())

        result = validate_architecture_coverage(mapping, self.behavior, self.lifecycle, evidence_root=self.repo)

        self.assertEqual(result["status"], "CLEAR")
        self.assertEqual(result["invariant_count"], len(REQUIRED_ARCHITECTURE_INVARIANTS))

    def test_missing_behavior_suite_expected_file_or_invariant_fails_closed(self) -> None:
        missing_suite = deepcopy(self.mapping)
        missing_suite["suites"].pop("ik-models")
        with self.assertRaises(LifecycleBlockedError) as suite:
            validate_architecture_coverage(missing_suite, self.behavior, self.lifecycle, evidence_root=self.repo)
        self.assertEqual(suite.exception.code, "architecture_suite_missing")

        missing_file = deepcopy(self.behavior)
        missing_file["selected_paths"].pop()
        with self.assertRaises(LifecycleBlockedError) as path:
            validate_architecture_coverage(self.mapping, missing_file, self.lifecycle, evidence_root=self.repo)
        self.assertEqual(path.exception.code, "behavior_test_file_set_invalid")

        missing_invariant = deepcopy(self.mapping)
        missing_invariant["invariants"].pop(REQUIRED_ARCHITECTURE_INVARIANTS[0])
        with self.assertRaises(LifecycleBlockedError) as invariant:
            validate_architecture_coverage(missing_invariant, self.behavior, self.lifecycle, evidence_root=self.repo)
        self.assertEqual(invariant.exception.code, "architecture_invariant_missing")

    def test_mapping_rejects_unknown_test_and_v3_plan_is_ineligible(self) -> None:
        unknown = deepcopy(self.mapping)
        unknown["invariants"][REQUIRED_ARCHITECTURE_INVARIANTS[0]]["test_ids"] = ["missing::test"]
        with self.assertRaises(LifecycleBlockedError) as test_id:
            validate_architecture_coverage(unknown, self.behavior, self.lifecycle, evidence_root=self.repo)
        self.assertEqual(test_id.exception.code, "architecture_test_mapping_invalid")

        v3 = json.loads((self.repo / "docs/planning-receipts/2026-08-21-hermes-corrected-composed-execution-plan-v3.json").read_text())
        with self.assertRaises(LifecycleBlockedError) as old:
            validate_v4_execution_plan(v3)
        self.assertEqual(old.exception.code, "v4_execution_plan_schema_invalid")


if __name__ == "__main__":
    unittest.main()
