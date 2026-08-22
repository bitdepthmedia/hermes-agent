from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from ik_lifecycle.model_artifacts import (
    ArtifactFile,
    ArtifactSpec,
    ModelArtifactError,
    clone_ollama_model,
    prepare_artifact_root,
    validate_artifact_spec,
    verify_artifact_files,
)


class ModelArtifactGateTests(unittest.TestCase):
    def _spec(self) -> ArtifactSpec:
        return ArtifactSpec(
            source_repository="Qwen/Qwen3.8-27B",
            source_revision="1" * 40,
            artifact_repository="ggml-org/Qwen3.8-27B-GGUF",
            artifact_revision="2" * 40,
            license_id="apache-2.0",
            model_card_sha256="3" * 64,
            license_sha256="4" * 64,
            config_sha256="9" * 64,
            chat_template_sha256="a" * 64,
            source_link_sha256="5" * 64,
            converter_log_sha256="6" * 64,
            quantizer_revision="7" * 40,
            runtime_id="ollama-0.32.15",
            runtime_sha256="8" * 64,
            files=(
                ArtifactFile("model.gguf", 4, hashlib.sha256(b"test").hexdigest(), "model"),
                ArtifactFile("mmproj.gguf", 4, hashlib.sha256(b"view").hexdigest(), "projector"),
            ),
        )

    def test_official_source_and_conversion_chain_are_required(self) -> None:
        self.assertEqual(validate_artifact_spec(self._spec()), "CLEAR")
        bad = self._spec().__dict__ | {"source_repository": "third-party/modified"}
        with self.assertRaisesRegex(ModelArtifactError, "source_repository_ineligible"):
            validate_artifact_spec(ArtifactSpec(**bad))

    def test_store_is_outside_git_cloud_and_profile_roots_and_symlink_safe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            git_root = root / "repo"
            (git_root / ".git").mkdir(parents=True)
            with self.assertRaisesRegex(ModelArtifactError, "artifact_root_forbidden"):
                prepare_artifact_root(git_root / "models", forbidden_roots=(git_root,))
            safe = prepare_artifact_root(root / "state" / "models", forbidden_roots=(git_root,))
            self.assertEqual(safe.stat().st_mode & 0o777, 0o700)
            link = root / "linked"
            link.symlink_to(safe, target_is_directory=True)
            with self.assertRaisesRegex(ModelArtifactError, "artifact_root_symlink"):
                prepare_artifact_root(link, forbidden_roots=(git_root,))

    def test_whole_file_hash_size_permissions_and_tamper_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            os.chmod(root, 0o700)
            (root / "model.gguf").write_bytes(b"test")
            (root / "mmproj.gguf").write_bytes(b"view")
            os.chmod(root / "model.gguf", 0o400)
            os.chmod(root / "mmproj.gguf", 0o400)
            receipt = verify_artifact_files(root, self._spec())
            self.assertEqual(receipt["status"], "CLEAR")
            self.assertNotIn(str(root), json.dumps(receipt))
            os.chmod(root / "model.gguf", 0o600)
            (root / "model.gguf").write_bytes(b"evil")
            with self.assertRaisesRegex(ModelArtifactError, "artifact_integrity_failed"):
                verify_artifact_files(root, self._spec())

    def test_missing_projector_or_latest_revision_is_rejected(self) -> None:
        missing = self._spec().__dict__ | {"files": self._spec().files[:1]}
        with self.assertRaisesRegex(ModelArtifactError, "required_roles_missing"):
            validate_artifact_spec(ArtifactSpec(**missing))
        latest = self._spec().__dict__ | {"artifact_revision": "latest"}
        with self.assertRaisesRegex(ModelArtifactError, "artifact_revision_invalid"):
            validate_artifact_spec(ArtifactSpec(**latest))
        unbound_template = self._spec().__dict__ | {"chat_template_sha256": "unbound"}
        with self.assertRaisesRegex(ModelArtifactError, "provenance_digest_invalid"):
            validate_artifact_spec(ArtifactSpec(**unbound_template))

    def test_ollama_baseline_snapshot_is_content_bound_and_tamper_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            destination = root / "destination"
            manifest = source / "manifests/registry.ollama.ai/library/fixture/q4"
            blob_content = b"baseline"
            blob_digest = hashlib.sha256(blob_content).hexdigest()
            blob = source / "blobs" / f"sha256-{blob_digest}"
            blob.parent.mkdir(parents=True)
            blob.write_bytes(blob_content)
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({
                "schemaVersion": 2,
                "config": {"digest": f"sha256:{blob_digest}", "size": len(blob_content)},
                "layers": [],
            }), encoding="utf-8")
            manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()
            receipt = clone_ollama_model(source, destination, "fixture:q4", manifest_sha)
            self.assertEqual((receipt["status"], receipt["blob_count"]), ("CLEAR", 1))
            self.assertNotIn(str(source), json.dumps(receipt))
            blob.write_bytes(b"tampered")
            with self.assertRaisesRegex(ModelArtifactError, "baseline_blob_integrity_failed"):
                clone_ollama_model(source, destination / "other", "fixture:q4", manifest_sha)


if __name__ == "__main__":
    unittest.main()
