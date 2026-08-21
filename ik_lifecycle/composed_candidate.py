"""Deterministic immutable composition and isolated writable build roots."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from typing import Any

from .composed_source import OverlayManifest, compose_source, tree_digest
from .models import LifecycleBlockedError


SCHEMA_ID = "ik.hermes.composed-candidate.v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _make_writable(root: Path) -> None:
    root.chmod(stat.S_IMODE(root.stat().st_mode) | 0o700)
    for path in root.rglob("*"):
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode | (0o700 if path.is_dir() else 0o600))


@dataclass(frozen=True)
class ComposedCandidateInputs:
    official_source: Path
    official_tree_sha256: str
    overlay_root: Path
    overlay_manifest: OverlayManifest
    replay_manifest_sha256: str
    implementation_commit: str
    isolated_root: Path
    protected_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ComposedCandidate:
    composition_id: str
    immutable_source: Path
    build_root: Path
    manifest_path: Path
    composed_tree_sha256: str


def _identity(inputs: ComposedCandidateInputs) -> dict[str, str]:
    return {
        "target_tag": inputs.overlay_manifest.target_tag,
        "target_commit_sha": inputs.overlay_manifest.target_commit_sha,
        "official_tree_sha256": inputs.official_tree_sha256,
        "overlay_manifest_sha256": inputs.overlay_manifest.digest(),
        "overlay_source_sha256": inputs.overlay_manifest.source_digest,
        "replay_manifest_sha256": inputs.replay_manifest_sha256,
        "implementation_commit": inputs.implementation_commit,
    }


def _validate_existing(
    manifest_path: Path,
    expected_identity: dict[str, str],
    immutable_source: Path,
    build_root: Path,
) -> ComposedCandidate:
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleBlockedError("composed_candidate_tampered", "composed candidate manifest is unreadable") from exc
    claimed = document.get("manifest_sha256")
    unsigned = {key: value for key, value in document.items() if key != "manifest_sha256"}
    if (
        document.get("schema_id") != SCHEMA_ID
        or claimed != hashlib.sha256(_canonical(unsigned)).hexdigest()
        or document.get("identity") != expected_identity
        or not immutable_source.is_dir()
        or not build_root.is_dir()
    ):
        raise LifecycleBlockedError("composed_candidate_tampered", "composed candidate identity changed")
    expected_tree = document.get("composed_tree_sha256")
    if tree_digest(immutable_source) != expected_tree or tree_digest(build_root, excluded_names=("node_modules",)) != document.get("build_root_pristine_sha256"):
        raise LifecycleBlockedError("composed_candidate_tampered", "composed candidate tree changed")
    return ComposedCandidate(document["composition_id"], immutable_source, build_root, manifest_path, expected_tree)


def construct_composed_candidate(inputs: ComposedCandidateInputs) -> ComposedCandidate:
    """Build an immutable composition and pristine writable copy without executing it."""

    official = Path(inputs.official_source).resolve()
    overlay = Path(inputs.overlay_root).resolve()
    isolated = Path(inputs.isolated_root).resolve()
    protected = tuple(Path(item).resolve() for item in inputs.protected_paths)
    for protected_root in protected:
        if _inside(isolated, protected_root) or _inside(protected_root, isolated):
            raise LifecycleBlockedError("composed_candidate_protected_path", "isolated candidate root overlaps a protected path")
    for source_root in (official, overlay):
        if _inside(isolated, source_root) or _inside(source_root, isolated):
            raise LifecycleBlockedError("composed_candidate_source_overlap", "isolated candidate root overlaps a source root")
    actual_source_digest = tree_digest(official)
    if actual_source_digest != inputs.official_tree_sha256:
        raise LifecycleBlockedError("composed_official_source_drift", "official source tree changed")
    identity = _identity(inputs)
    composition_id = hashlib.sha256(_canonical(identity)).hexdigest()[:24]
    composition_root = isolated / "compositions" / composition_id
    immutable_source = composition_root / "source"
    manifest_path = composition_root / "manifest.json"
    build_root = isolated / "build-roots" / composition_id / "worktree"
    if manifest_path.exists() or immutable_source.exists() or build_root.exists():
        if manifest_path.exists() and immutable_source.exists() and build_root.exists():
            return _validate_existing(manifest_path, identity, immutable_source, build_root)
        raise LifecycleBlockedError("composed_candidate_tampered", "composed candidate is incomplete")
    composition_root.mkdir(parents=True, exist_ok=False)
    build_root.parent.mkdir(parents=True, exist_ok=False)
    try:
        composed = compose_source(official, overlay, immutable_source, inputs.overlay_manifest)
        shutil.copytree(immutable_source, build_root, symlinks=False)
        _make_writable(build_root)
        pristine = tree_digest(build_root)
        if pristine != composed.tree_digest:
            raise LifecycleBlockedError("composed_candidate_copy_mismatch", "writable build root differs from immutable composition")
        document = {
            "schema_id": SCHEMA_ID,
            "status": "PREPARED",
            "composition_id": composition_id,
            "identity": identity,
            "immutable_source": str(immutable_source),
            "build_root": str(build_root),
            "composed_tree_sha256": composed.tree_digest,
            "build_root_pristine_sha256": pristine,
        }
        document["manifest_sha256"] = hashlib.sha256(_canonical(document)).hexdigest()
        manifest_path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        manifest_path.chmod(0o400)
        return ComposedCandidate(composition_id, immutable_source, build_root, manifest_path, composed.tree_digest)
    except Exception:
        # Preserve an incomplete root for forensic inspection; never reuse it.
        failure = composition_root / "FAILED"
        try:
            failure.write_text("construction_failed\n", encoding="utf-8")
        except OSError:
            pass
        raise
