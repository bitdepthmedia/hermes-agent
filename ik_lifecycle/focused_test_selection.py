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


def _discover(root: Path, selected_paths: tuple[str, ...]) -> tuple[unittest.TestSuite, dict]:
    base = Path(root).resolve()
    if not base.is_dir() or not selected_paths or len(set(selected_paths)) != len(selected_paths):
        raise LifecycleBlockedError("focused_test_path_drift", "focused test root or selection is invalid")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    files: list[dict[str, str]] = []
    for index, relative in enumerate(selected_paths):
        module, digest = _load(base, relative, index)
        discovered = loader.loadTestsFromModule(module)
        if loader.errors:
            raise LifecycleBlockedError("focused_test_import_failed", f"unittest discovery failed: {relative}")
        suite.addTests(discovered)
        files.append({"path": relative, "sha256": digest})
    test_count = suite.countTestCases()
    if test_count < 1:
        raise LifecycleBlockedError("focused_test_selection_empty", "focused lifecycle selection discovered zero tests")
    binding = {
        "schema_id": "ik.hermes.focused-lifecycle-discovery.v1",
        "status": "CLEAR",
        "python": sys.executable,
        "python_version": ".".join(str(item) for item in sys.version_info[:3]),
        "selected_paths": list(selected_paths),
        "files": files,
        "module_count": len(files),
        "test_count": test_count,
        "mode": "compile_import_discovery_only",
    }
    binding["selection_sha256"] = hashlib.sha256(
        json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return suite, binding


def discover_focused_tests(root: Path, selected_paths: tuple[str, ...] = DEFAULT_FOCUSED_TEST_PATHS) -> dict:
    """Compile, import and count the exact suite without running test bodies."""

    _, binding = _discover(root, selected_paths)
    return binding


def execute_focused_tests(root: Path, selected_paths: tuple[str, ...] = DEFAULT_FOCUSED_TEST_PATHS) -> unittest.result.TestResult:
    """Execute exactly the suite previously supported by discovery."""

    suite, _ = _discover(root, selected_paths)
    return unittest.TextTestRunner(verbosity=2).run(suite)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--discover-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.discover_only:
        print(json.dumps(discover_focused_tests(args.root), sort_keys=True))
        return 0
    return 0 if execute_focused_tests(args.root).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
