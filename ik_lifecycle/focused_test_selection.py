"""Fail-closed discovery and execution for the dependency-free lifecycle suite."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import sys
import unittest

from .models import LifecycleBlockedError


DEFAULT_FOCUSED_TEST_PATHS = (
    "tests/ik_lifecycle/test_audited_dependency_copy.py",
    "tests/ik_lifecycle/test_cells_health_rollback.py",
    "tests/ik_lifecycle/test_composed_candidate.py",
    "tests/ik_lifecycle/test_composed_execution_authority.py",
    "tests/ik_lifecycle/test_composed_release.py",
    "tests/ik_lifecycle/test_continuity_migration.py",
    "tests/ik_lifecycle/test_corrected_execution_contract.py",
    "tests/ik_lifecycle/test_execution_plan.py",
    "tests/ik_lifecycle/test_focused_test_selection.py",
    "tests/ik_lifecycle/test_macos_network_guard.py",
    "tests/ik_lifecycle/test_sealing_split.py",
    "tests/ik_lifecycle/test_v4_architecture_execution.py",
)

BEHAVIOR_TEST_PATHS = (
    "tests/ik_orchestration/test_envelope.py",
    "tests/ik_orchestration/test_execution_ladder.py",
    "tests/ik_orchestration/test_learning.py",
    "tests/ik_orchestration/test_nate_os_boundaries.py",
    "tests/ik_orchestration/test_plugin_registration.py",
    "tests/ik_orchestration/test_privacy.py",
    "tests/ik_orchestration/test_reintegration.py",
    "tests/ik_orchestration/test_routing.py",
    "tests/ik_orchestration/test_store_transport_availability.py",
    "tests/ik_models/test_eval_harness.py",
    "tests/ik_models/test_model_workers.py",
)

# These checks validate repository planning history rather than the composed
# candidate. They remain in the normal repository suite, but are deliberately
# excluded from the portable candidate lifecycle batch.
LIFECYCLE_REPO_ONLY_TEST_IDS = (
    "tests/ik_lifecycle/test_composed_execution_authority.py::ComposedExecutionAuthorityTests.test_old_upstream_only_plan_cannot_authorize_composed_execution",
    "tests/ik_lifecycle/test_composed_release.py::ComposedReleaseTests.test_declared_overlay_is_bound_to_target_and_reviewed_replay",
    "tests/ik_lifecycle/test_execution_plan.py::ExecutionPlanTests.test_committed_candidate_execution_plan_is_valid_and_non_executable",
    "tests/ik_lifecycle/test_execution_plan.py::ExecutionPlanTests.test_committed_composed_plan_and_decision_input_are_digest_bound_and_non_executable",
    "tests/ik_lifecycle/test_execution_plan.py::ExecutionPlanTests.test_committed_next_approval_input_self_digest_is_valid",
    "tests/ik_lifecycle/test_v4_architecture_execution.py::V4ArchitectureExecutionTests.test_mapping_rejects_unknown_test_and_v3_plan_is_ineligible",
)


def _relative(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        raise LifecycleBlockedError("focused_test_path_drift", "focused test path is not a confined Python file")
    return Path(*path.parts)


def _load(root: Path, relative: str, index: int):
    path = root / _relative(relative)
    if not path.is_file() or path.is_symlink():
        raise LifecycleBlockedError("focused_test_path_drift", f"focused test path is unavailable: {relative}")
    try:
        source = path.read_bytes()
        compile(source, str(path), "exec", dont_inherit=True)
        name = f"_ik_focused_{index}_{hashlib.sha256(relative.encode()).hexdigest()[:16]}"
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError("no import loader")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module, hashlib.sha256(source).hexdigest()
    except LifecycleBlockedError:
        raise
    except Exception as exc:
        raise LifecycleBlockedError("focused_test_import_failed", f"focused test import failed: {relative}") from exc


def _test_ids(suite: unittest.TestSuite, relative: str) -> list[str]:
    result: list[str] = []
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            result.extend(_test_ids(test, relative))
        else:
            method = getattr(test, "_testMethodName", None)
            if not isinstance(method, str):
                raise LifecycleBlockedError("focused_test_id_invalid", "focused test id cannot be normalized")
            result.append(f"{relative}::{test.__class__.__name__}.{method}")
    return result


def _tests(suite: unittest.TestSuite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from _tests(test)
        else:
            yield test


def _discover(root: Path, selected_paths: tuple[str, ...], suite_id: str) -> tuple[unittest.TestSuite, dict]:
    base = Path(root).resolve()
    if not base.is_dir() or not selected_paths or len(set(selected_paths)) != len(selected_paths):
        raise LifecycleBlockedError("focused_test_path_drift", "focused test root or selection is invalid")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    files: list[dict[str, str]] = []
    test_ids: list[str] = []
    excluded: set[str] = set()
    for index, relative in enumerate(selected_paths):
        module, digest = _load(base, relative, index)
        discovered = loader.loadTestsFromModule(module)
        if loader.errors:
            raise LifecycleBlockedError("focused_test_import_failed", f"unittest discovery failed: {relative}")
        discovered_ids = _test_ids(discovered, relative)
        for test, test_id in zip(_tests(discovered), discovered_ids, strict=True):
            if suite_id == "lifecycle" and test_id in LIFECYCLE_REPO_ONLY_TEST_IDS:
                excluded.add(test_id)
                continue
            suite.addTest(test)
            test_ids.append(test_id)
        files.append({"path": relative, "sha256": digest})
    test_count = suite.countTestCases()
    if test_count < 1 or len(test_ids) != test_count or len(set(test_ids)) != test_count:
        raise LifecycleBlockedError("focused_test_selection_empty", "focused lifecycle selection discovered zero tests")
    binding = {
        "schema_id": "ik.hermes.focused-test-discovery.v2",
        "suite_id": suite_id,
        "status": "CLEAR",
        "python": sys.executable,
        "python_version": ".".join(str(item) for item in sys.version_info[:3]),
        "selected_paths": list(selected_paths),
        "files": files,
        "module_count": len(files),
        "test_count": test_count,
        "test_ids": test_ids,
        "excluded_test_ids": [test_id for test_id in LIFECYCLE_REPO_ONLY_TEST_IDS if test_id in excluded],
        "mode": "compile_import_discovery_only",
    }
    binding["selection_sha256"] = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return suite, binding


def discover_focused_tests(
    root: Path,
    selected_paths: tuple[str, ...] = DEFAULT_FOCUSED_TEST_PATHS,
    *,
    suite_id: str = "lifecycle",
) -> dict:
    """Compile, import and count the exact suite without running test bodies."""

    _, binding = _discover(root, selected_paths, suite_id)
    return binding


def execute_focused_tests(
    root: Path,
    selected_paths: tuple[str, ...] = DEFAULT_FOCUSED_TEST_PATHS,
    *,
    suite_id: str = "lifecycle",
) -> unittest.result.TestResult:
    """Execute exactly the suite previously supported by discovery."""

    suite, _ = _discover(root, selected_paths, suite_id)
    return unittest.TextTestRunner(verbosity=2).run(suite)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--suite", choices=("lifecycle", "behavior"), default="lifecycle")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--discover-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    selected = BEHAVIOR_TEST_PATHS if args.suite == "behavior" else DEFAULT_FOCUSED_TEST_PATHS
    if args.discover_only:
        print(json.dumps(discover_focused_tests(args.root, selected, suite_id=args.suite), sort_keys=True))
        return 0
    return 0 if execute_focused_tests(args.root, selected, suite_id=args.suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
