from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ik_lifecycle.candidate import Candidate, seal_candidate
from ik_lifecycle.models import GateSet, LifecycleBlockedError
from ik_lifecycle.release_bundle import ArtifactBinding, ReleaseBundleInputs, build_release_bundle


class SealingSplitTests(unittest.TestCase):
    def test_upstream_only_candidate_cannot_be_sealed_or_packaged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"; source.mkdir()
            manifest = root / "build-manifest.json"
            manifest.write_text(json.dumps({"status": "STATIC_PREPARED"}))
            candidate = Candidate("candidate", root, manifest, source, None)  # layout is unused at this fail-closed boundary
            gates = GateSet(True, True, True, True, True)
            with self.assertRaises(LifecycleBlockedError) as error:
                seal_candidate(candidate, gates)
            self.assertEqual(error.exception.code, "composed_release_bundle_required")

    def test_code_sealing_accepts_bound_bundle_without_profile_or_rollback_pair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"; source.mkdir(); (source / "code").write_text("composed")
            runtime = root / "runtime"; runtime.mkdir(); (runtime / "python").write_text("pinned")
            manifest = root / "build-manifest.json"
            manifest.write_text(json.dumps({
                "status": "STATIC_PREPARED",
                "release_selection": {"target": {"tag": "v2026.8.18"}},
                "source": {"commit_sha": "e" * 40},
            }))
            candidate = Candidate("candidate", root, manifest, source, None)
            bundle = build_release_bundle(
                ReleaseBundleInputs("candidate", "v2026.8.18", "e" * 40, source, (ArtifactBinding("runtime", runtime),)),
                root / "releases",
            )
            gates = GateSet(True, True, True, True, True, release_bundle_manifest=bundle.manifest_path)
            with patch("ik_lifecycle.candidate._validate_dependency_receipts"):
                sealed = seal_candidate(candidate, gates)
                repeated = seal_candidate(candidate, gates)
            result = json.loads(manifest.read_text())
            self.assertEqual((sealed, repeated, result["status"]), (bundle.root, bundle.root, "CODE_SEALED"))
            self.assertFalse(result["sealed_release"]["profile_pairing_performed"])
            self.assertFalse(result["sealed_release"]["rollback_pairing_performed"])


if __name__ == "__main__":
    unittest.main()
