from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ik_lifecycle.focused_test_selection import (
    DEFAULT_FOCUSED_TEST_PATHS,
    LIFECYCLE_REPO_ONLY_TEST_IDS,
    discover_focused_tests,
)
from ik_lifecycle.models import LifecycleBlockedError


class FocusedLifecycleSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _write(self, relative: str, source: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    def test_zero_tests_fail_closed(self) -> None:
        self._write("tests/ik_lifecycle/test_empty.py", "VALUE = 1\n")

        with self.assertRaises(LifecycleBlockedError) as error:
            discover_focused_tests(self.root, ("tests/ik_lifecycle/test_empty.py",))

        self.assertEqual(error.exception.code, "focused_test_selection_empty")

    def test_missing_or_unimportable_test_path_fails_closed(self) -> None:
        with self.assertRaises(LifecycleBlockedError) as missing:
            discover_focused_tests(self.root, ("tests/ik_lifecycle/test_missing.py",))
        self.assertEqual(missing.exception.code, "focused_test_path_drift")

        self._write("tests/ik_lifecycle/test_bad.py", "raise RuntimeError('import drift')\n")
        with self.assertRaises(LifecycleBlockedError) as imported:
            discover_focused_tests(self.root, ("tests/ik_lifecycle/test_bad.py",))
        self.assertEqual(imported.exception.code, "focused_test_import_failed")

    def test_discovery_compiles_imports_and_counts_without_running_tests(self) -> None:
        self._write(
            "tests/ik_lifecycle/test_safe.py",
            "import unittest\n"
            "class SafeTests(unittest.TestCase):\n"
            "    def test_not_executed_during_discovery(self):\n"
            "        raise AssertionError('discovery ran a test body')\n",
        )

        proof = discover_focused_tests(self.root, ("tests/ik_lifecycle/test_safe.py",))

        self.assertEqual(proof["status"], "CLEAR")
        self.assertEqual(proof["test_count"], 1)
        self.assertEqual(proof["module_count"], 1)
        self.assertEqual(proof["selected_paths"], ["tests/ik_lifecycle/test_safe.py"])
        self.assertRegex(proof["selection_sha256"], r"^[0-9a-f]{64}$")
        json.dumps(proof, sort_keys=True)

    def test_real_declared_selection_exists_and_is_nonzero(self) -> None:
        repo = Path(__file__).resolve().parents[2]

        proof = discover_focused_tests(repo, DEFAULT_FOCUSED_TEST_PATHS)

        self.assertEqual(proof["test_count"], 46)
        self.assertEqual(proof["module_count"], len(DEFAULT_FOCUSED_TEST_PATHS))
        self.assertEqual(proof["excluded_test_ids"], list(LIFECYCLE_REPO_ONLY_TEST_IDS))
        self.assertTrue(set(proof["test_ids"]).isdisjoint(LIFECYCLE_REPO_ONLY_TEST_IDS))


if __name__ == "__main__":
    unittest.main()
