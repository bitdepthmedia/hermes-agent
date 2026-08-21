"""Artifact-bound code release bundle, deliberately separate from profiles."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

from .composed_source import tree_digest
from .models import LifecycleBlockedError


@dataclass(frozen=True)
class ArtifactBinding:
    artifact_id: str
    path: Path


@dataclass(frozen=True)
class ReleaseBundleInputs:
    candidate_id: str
    target_tag: str
    target_commit_sha: str
    composed_source: Path
    artifacts: tuple[ArtifactBinding, ...]


@dataclass(frozen=True)
class SealedReleaseBundle:
    bundle_id: str
    root: Path
    manifest_path: Path


def _read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def build_release_bundle(inputs: ReleaseBundleInputs, release_root: Path) -> SealedReleaseBundle:
    source = Path(inputs.composed_source).resolve()
    bindings: dict[str, dict[str, str]] = {"composed-source": {"tree_sha256": tree_digest(source)}}
    artifact_paths: list[tuple[ArtifactBinding, Path]] = []
    for artifact in inputs.artifacts:
        path = Path(artifact.path).resolve()
        if not path.is_dir() or path.is_symlink() or artifact.artifact_id in bindings:
            raise LifecycleBlockedError("release_artifact_invalid", "release artifact binding is invalid")
        bindings[artifact.artifact_id] = {"tree_sha256": tree_digest(path)}
        artifact_paths.append((artifact, path))
    identity = {
        "candidate_id": inputs.candidate_id,
        "target_tag": inputs.target_tag,
        "target_commit_sha": inputs.target_commit_sha,
        "bindings": bindings,
    }
    bundle_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
    root = Path(release_root).resolve() / bundle_id
    if root.exists():
        manifest = root / "release-manifest.json"
        if not manifest.is_file() or json.loads(manifest.read_text()).get("identity") != identity:
            raise LifecycleBlockedError("release_bundle_collision", "existing release bundle does not match inputs")
        return SealedReleaseBundle(bundle_id, root, manifest)
    staging = root.with_name(f".{bundle_id}.{os.getpid()}.staging")
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.mkdir()
    try:
        shutil.copytree(source, staging / "source")
        for artifact, path in artifact_paths:
            shutil.copytree(path, staging / "artifacts" / artifact.artifact_id)
        manifest_path = staging / "release-manifest.json"
        manifest_path.write_text(json.dumps({"schema_id": "ik.hermes.release-bundle.v1", "status": "SEALED_CODE_ONLY", "bundle_id": bundle_id, "identity": identity}, sort_keys=True, separators=(",", ":")) + "\n")
        _read_only(staging)
        os.replace(staging, root)
        return SealedReleaseBundle(bundle_id, root, root / "release-manifest.json")
    except Exception:
        if staging.exists(): shutil.rmtree(staging)
        raise
