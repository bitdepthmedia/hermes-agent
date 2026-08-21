from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ik_lifecycle.composed_candidate import ComposedCandidateInputs, construct_composed_candidate
from ik_lifecycle.composed_source import OverlayManifest, tree_digest
from ik_lifecycle.models import LifecycleBlockedError


class ComposedCandidateConstructionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.official = self.root / "official"
        self.overlay = self.root / "overlay"
        self.output = self.root / "isolated"
        self.official.mkdir(); self.overlay.mkdir(); self.output.mkdir()
        (self.official / "upstream.py").write_text("official\n", encoding="utf-8")
        (self.overlay / "extension.py").write_text("declared\n", encoding="utf-8")
        self.manifest = OverlayManifest("v2026.8.18", "e" * 40, (("extension.py", "ik_extensions/extension.py"),), "a" * 64)
        self.inputs = ComposedCandidateInputs(
            official_source=self.official,
            official_tree_sha256=tree_digest(self.official),
            overlay_root=self.overlay,
            overlay_manifest=self.manifest,
            replay_manifest_sha256="b" * 64,
            implementation_commit="ec392f4a34b88585d2f874f47d616f32cde20520",
            isolated_root=self.output,
            protected_paths=(self.root / "running",),
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_constructs_immutable_composed_source_and_separate_writable_build_root(self) -> None:
        candidate = construct_composed_candidate(self.inputs)

        self.assertEqual((candidate.immutable_source / "upstream.py").read_text(), "official\n")
        self.assertEqual((candidate.build_root / "ik_extensions/extension.py").read_text(), "declared\n")
        self.assertFalse(candidate.immutable_source.stat().st_mode & 0o222)
        self.assertTrue(candidate.build_root.stat().st_mode & 0o200)
        receipt = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["composed_tree_sha256"], candidate.composed_tree_sha256)
        self.assertEqual(receipt["build_root_pristine_sha256"], candidate.composed_tree_sha256)

    def test_construction_is_idempotent_and_tamper_fails_closed(self) -> None:
        first = construct_composed_candidate(self.inputs)
        dependencies = first.build_root / "node_modules/safe"
        dependencies.mkdir(parents=True)
        (dependencies / "package.json").write_text('{"name":"safe","version":"1.0.0"}')
        second = construct_composed_candidate(self.inputs)
        self.assertEqual(first, second)

        target = first.build_root / "upstream.py"
        target.write_text("tampered\n", encoding="utf-8")
        with self.assertRaises(LifecycleBlockedError) as error:
            construct_composed_candidate(self.inputs)
        self.assertEqual(error.exception.code, "composed_candidate_tampered")

    def test_source_drift_overlap_symlink_and_running_root_fail_closed(self) -> None:
        bad = self.inputs.__class__(**{**self.inputs.__dict__, "official_tree_sha256": "f" * 64})
        with self.assertRaises(LifecycleBlockedError) as error:
            construct_composed_candidate(bad)
        self.assertEqual(error.exception.code, "composed_official_source_drift")

        (self.official / "link").symlink_to("upstream.py")
        drifted = self.inputs.__class__(**{**self.inputs.__dict__, "official_tree_sha256": "0" * 64})
        with self.assertRaises(LifecycleBlockedError) as error:
            construct_composed_candidate(drifted)
        self.assertIn(error.exception.code, ("composed_symlink", "composed_official_source_drift"))

        clean_source = self.root / "clean"
        clean_source.mkdir(); (clean_source / "code").write_text("x")
        overlap = self.inputs.__class__(**{
            **self.inputs.__dict__,
            "official_source": clean_source,
            "official_tree_sha256": tree_digest(clean_source),
            "isolated_root": self.root / "running" / "candidate",
        })
        with self.assertRaises(LifecycleBlockedError) as error:
            construct_composed_candidate(overlap)
        self.assertEqual(error.exception.code, "composed_candidate_protected_path")


if __name__ == "__main__":
    unittest.main()
