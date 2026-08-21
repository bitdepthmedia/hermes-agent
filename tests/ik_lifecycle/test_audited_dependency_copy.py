from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from ik_lifecycle.audited_dependencies import materialize_audited_dependencies
from ik_lifecycle.models import LifecycleBlockedError


class AuditedDependencyCopyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.audit = self.root / "audit"
        self.build = self.root / "build"
        package = self.audit / "node_modules" / "safe-package"
        binary = self.audit / "node_modules" / ".bin"
        package.mkdir(parents=True); binary.mkdir()
        (package / "package.json").write_text('{"name":"safe-package","version":"1.0.0"}')
        (package / "cli.js").write_text("safe")
        (binary / "safe").symlink_to("../safe-package/cli.js")
        self.build.mkdir()

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_copies_only_declared_audited_dependency_roots_and_is_idempotent(self) -> None:
        first = materialize_audited_dependencies(self.audit, self.build, ("node_modules",))
        second = materialize_audited_dependencies(self.audit, self.build, ("node_modules",))

        self.assertEqual(first, second)
        self.assertEqual((self.build / "node_modules/safe-package/cli.js").read_text(), "safe")
        self.assertTrue((self.build / "node_modules/.bin/safe").is_symlink())
        self.assertEqual(first.status, "CLEAR")

    def test_tamper_unsafe_symlink_and_forbidden_installed_metadata_fail_closed(self) -> None:
        materialize_audited_dependencies(self.audit, self.build, ("node_modules",))
        (self.build / "node_modules/safe-package/cli.js").write_text("tampered")
        with self.assertRaises(LifecycleBlockedError) as error:
            materialize_audited_dependencies(self.audit, self.build, ("node_modules",))
        self.assertEqual(error.exception.code, "audited_dependency_copy_tampered")

        other = self.root / "other"; other.mkdir()
        (other / "node_modules").mkdir(); (other / "node_modules/escape").symlink_to("/tmp")
        with self.assertRaises(LifecycleBlockedError) as error:
            materialize_audited_dependencies(other, self.root / "other-build", ("node_modules",))
        self.assertEqual(error.exception.code, "audited_dependency_symlink_invalid")

        forbidden = self.root / "forbidden"; package = forbidden / "node_modules/x"; package.mkdir(parents=True)
        (package / "package.json").write_text('{"name":"axios","version":"1.14.1"}')
        with self.assertRaises(LifecycleBlockedError) as error:
            materialize_audited_dependencies(forbidden, self.root / "forbidden-build", ("node_modules",))
        self.assertEqual(error.exception.code, "audited_dependency_forbidden_version")


if __name__ == "__main__":
    unittest.main()
