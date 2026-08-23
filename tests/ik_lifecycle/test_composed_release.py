from __future__ import annotations

import json
from pathlib import Path
import stat
import tempfile
import unittest

from ik_lifecycle.composed_source import OverlayManifest, compose_source, load_declared_overlay
from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.network_guard import DeniedNetworkAdapter, run_network_denied
from ik_lifecycle.release_bundle import ArtifactBinding, ReleaseBundleInputs, build_release_bundle


class ComposedReleaseTests(unittest.TestCase):
    def test_declared_overlay_is_bound_to_target_and_reviewed_replay(self) -> None:
        root = Path(__file__).resolve().parents[2]
        manifest = load_declared_overlay(root, root / "ik_lifecycle/manifests/bert-ernie-overlay-v1.json")
        self.assertEqual((manifest.target_tag, manifest.target_commit_sha), ("v2026.8.18", "e624e9fde561e1add9388384012b295fde669ade"))
        self.assertTrue(any(source.endswith("persona_orchestration/envelope.py") for source, _ in manifest.entries))
        allowed_roots = (
            "ik_extensions/",
            "ik_cells/",
            "ik_lifecycle/",
            "plugins/model-providers/ik-ernie-local/",
            "tests/ik_lifecycle/",
            "tests/ik_orchestration/",
            "tests/ik_models/",
            "evals/ik/",
            "docs/architecture/",
        )
        exact_files = {
            "scripts/ik-bert-runtime-canary",
            "scripts/ik-cell-service",
            "scripts/ik-ernie-closed-runtime",
            "scripts/ik-ernie-runtime-canary",
            "tests/e2e/test_ik_bert_cell_fixture.py",
            "tests/e2e/test_ik_ernie_cell_fixture.py",
            "tests/e2e/test_ik_launchd_service_fixture.py",
            "docs/planning-receipts/2026-08-22-hermes-ernie-runtime-canary-v1.json",
        }
        self.assertTrue(
            all(source.startswith(allowed_roots) or source in exact_files for source, _ in manifest.entries)
        )
        self.assertTrue(any(source == "tests/ik_lifecycle/test_focused_test_selection.py" for source, _ in manifest.entries))
        behavior_tests = {
            source
            for source, _ in manifest.entries
            if source.startswith(("tests/ik_orchestration/test_", "tests/ik_models/test_"))
        }
        self.assertTrue(
            {
                "tests/ik_orchestration/test_approval_result.py",
                "tests/ik_models/test_model_workers.py",
                "tests/ik_models/test_offline_eval.py",
            }
            <= behavior_tests
        )
        self.assertTrue(any(source.startswith("evals/ik/") for source, _ in manifest.entries))
        self.assertTrue(
            any(source == "docs/architecture/bert-ernie-hermes-cell-architecture.md" for source, _ in manifest.entries)
        )
        self.assertTrue(
            exact_files
            <= {source for source, _ in manifest.entries}
        )

    def test_composition_is_deterministic_overlay_bound_and_does_not_mutate_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "official"
            overlay = root / "overlay"
            source.mkdir(); overlay.mkdir()
            (source / "upstream.py").write_text("official", encoding="utf-8")
            (overlay / "extension.py").write_text("declared", encoding="utf-8")
            manifest = OverlayManifest("v2026.8.18", "e" * 40, (("extension.py", "extensions/extension.py"),))
            first = compose_source(source, overlay, root / "composed-1", manifest)
            second = compose_source(source, overlay, root / "composed-2", manifest)
            self.assertEqual(first.tree_digest, second.tree_digest)
            self.assertEqual((source / "upstream.py").read_text(), "official")
            self.assertEqual((first.root / "extensions/extension.py").read_text(), "declared")
            with self.assertRaises(LifecycleBlockedError):
                compose_source(source, overlay, source / "bad", manifest)

    def test_read_only_official_snapshot_can_be_staged_without_mutating_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "official"
            overlay = root / "overlay"
            source.mkdir(); overlay.mkdir()
            upstream = source / "upstream.py"
            upstream.write_text("official", encoding="utf-8")
            (overlay / "extension.py").write_text("declared", encoding="utf-8")
            upstream.chmod(0o444); source.chmod(0o555)
            manifest = OverlayManifest("v2026.8.18", "e" * 40, (("extension.py", "extensions/extension.py"),))
            try:
                composed = compose_source(source, overlay, root / "composed", manifest)
                self.assertEqual((composed.root / "extensions/extension.py").read_text(), "declared")
                self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o555)
                self.assertEqual(stat.S_IMODE(upstream.stat().st_mode), 0o444)
            finally:
                source.chmod(0o755); upstream.chmod(0o644)

    def test_release_bundle_binds_composed_runtime_assets_and_split_sealing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            composed = root / "composed"; runtime = root / "runtime"; assets = root / "assets"
            for path in (composed, runtime, assets): path.mkdir()
            (composed / "code").write_text("overlay+official")
            (runtime / "python").write_text("pinned")
            (assets / "web").write_text("built")
            inputs = ReleaseBundleInputs("candidate-1", "v2026.8.18", "e" * 40, composed, (
                ArtifactBinding("runtime", runtime), ArtifactBinding("assets", assets)))
            bundle = build_release_bundle(inputs, root / "releases")
            manifest = json.loads(bundle.manifest_path.read_text())
            self.assertEqual(manifest["status"], "SEALED_CODE_ONLY")
            self.assertFalse((bundle.root / "profile-pointer.json").exists())
            self.assertFalse((bundle.root.stat().st_mode & 0o222))

    def test_network_denial_fails_closed_without_enforcement_proof(self) -> None:
        adapter = DeniedNetworkAdapter(enforced=True)
        receipt = run_network_denied(("synthetic", "test"), adapter)
        self.assertEqual(receipt.status, "CLEAR")
        with self.assertRaisesRegex(LifecycleBlockedError, "network"):
            run_network_denied(("synthetic",), DeniedNetworkAdapter(enforced=False))


if __name__ == "__main__":
    unittest.main()
