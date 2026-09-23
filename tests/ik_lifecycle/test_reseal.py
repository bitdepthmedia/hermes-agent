from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ik_lifecycle.composed_source import tree_digest
from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.release_bundle import ArtifactBinding, ReleaseBundleInputs, build_release_bundle
from ik_lifecycle.reseal import ResealInputs, recompose_and_reseal


class PostCanaryResealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.official = self.root / "official"
        self.overlay = self.root / "overlay"
        self.isolated = self.root / "isolated"
        self.prior_releases = self.root / "prior-releases"
        self.official.mkdir(); self.overlay.mkdir(); self.isolated.mkdir()
        (self.official / "upstream.py").write_text("official\n", encoding="utf-8")
        extension = self.overlay / "ik_lifecycle" / "monitor.py"
        extension.parent.mkdir()
        extension.write_text("disabled = True\n", encoding="utf-8")
        manifests = self.overlay / "manifests"
        manifests.mkdir()
        replay = manifests / "replay.json"
        replay.write_text('{"entries":[]}\n', encoding="utf-8")
        replay_sha = hashlib.sha256(replay.read_bytes()).hexdigest()
        overlay_sha = hashlib.sha256()
        overlay_sha.update(b"ik_lifecycle/monitor.py\0ik_lifecycle/monitor.py\0")
        overlay_sha.update(extension.read_bytes()); overlay_sha.update(b"\0")
        self.manifest = manifests / "overlay.json"
        self.manifest.write_text(json.dumps({
            "schema_id": "ik.hermes.extension-overlay-manifest.v1",
            "target": {"tag": "v2026.8.18", "commit_sha": "e" * 40},
            "replay_manifest": "replay.json",
            "replay_manifest_sha256": replay_sha,
            "overlay_source_sha256": overlay_sha.hexdigest(),
            "roots": ["ik_lifecycle"],
            "core_patch_count": 0,
        }), encoding="utf-8")
        prior_source = self.root / "prior-source"
        prior_source.mkdir(); (prior_source / "old").write_text("old")
        artifact = self.root / "artifact"
        artifact.mkdir(); (artifact / "built.js").write_text("built")
        self.prior = build_release_bundle(
            ReleaseBundleInputs("candidate", "v2026.8.18", "e" * 40, prior_source, (ArtifactBinding("ui", artifact),)),
            self.prior_releases,
        )
        self.inputs = ResealInputs(
            candidate_id="candidate",
            target_tag="v2026.8.18",
            target_commit_sha="e" * 40,
            official_source=self.official,
            official_tree_sha256=tree_digest(self.official),
            overlay_root=self.overlay,
            overlay_manifest_path=self.manifest,
            replay_manifest_sha256=replay_sha,
            implementation_commit="2" * 40,
            isolated_root=self.isolated,
            prior_release_root=self.prior.root,
            expected_prior_manifest_sha256=hashlib.sha256(self.prior.manifest_path.read_bytes()).hexdigest(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_recomposes_and_seals_new_source_while_reusing_only_bound_build_artifacts(self) -> None:
        before = tree_digest(self.prior.root)
        result = recompose_and_reseal(self.inputs)

        self.assertNotEqual(result.bundle.bundle_id, self.prior.bundle_id)
        self.assertEqual((result.bundle.root / "source/ik_lifecycle/monitor.py").read_text(), "disabled = True\n")
        self.assertEqual((result.bundle.root / "artifacts/ui/built.js").read_text(), "built")
        self.assertEqual(result.reused_artifact_ids, ("ui",))
        self.assertFalse(result.dependency_execution_performed)
        self.assertFalse(result.build_execution_performed)
        self.assertEqual(tree_digest(self.prior.root), before)

    def test_reseal_is_idempotent_and_preserves_prior_release(self) -> None:
        first = recompose_and_reseal(self.inputs)
        second = recompose_and_reseal(self.inputs)
        self.assertEqual(first.bundle, second.bundle)
        self.assertEqual(first.composed_tree_sha256, second.composed_tree_sha256)

    def test_prior_manifest_target_or_artifact_drift_fails_closed(self) -> None:
        with self.assertRaises(LifecycleBlockedError) as mismatch:
            recompose_and_reseal(replace(self.inputs, target_commit_sha="f" * 40))
        self.assertEqual(mismatch.exception.code, "reseal_overlay_target_mismatch")

        artifact = self.prior.root / "artifacts" / "ui" / "built.js"
        artifact.chmod(0o600); artifact.write_text("tampered")
        with self.assertRaises(LifecycleBlockedError) as tampered:
            recompose_and_reseal(self.inputs)
        self.assertEqual(tampered.exception.code, "reseal_prior_artifact_drift")


if __name__ == "__main__":
    unittest.main()
