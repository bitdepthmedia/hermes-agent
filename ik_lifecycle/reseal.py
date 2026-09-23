"""Deterministic post-overlay recomposition and code-only resealing."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from .composed_candidate import ComposedCandidateInputs, construct_composed_candidate
from .composed_source import load_declared_overlay, tree_digest
from .models import LifecycleBlockedError
from .release_bundle import ArtifactBinding, ReleaseBundleInputs, SealedReleaseBundle, build_release_bundle


@dataclass(frozen=True)
class ResealInputs:
    candidate_id: str
    target_tag: str
    target_commit_sha: str
    official_source: Path
    official_tree_sha256: str
    overlay_root: Path
    overlay_manifest_path: Path
    replay_manifest_sha256: str
    implementation_commit: str
    isolated_root: Path
    prior_release_root: Path
    expected_prior_manifest_sha256: str
    protected_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ResealResult:
    composition_id: str
    composed_tree_sha256: str
    overlay_manifest_sha256: str
    reused_artifact_ids: tuple[str, ...]
    prior_manifest_sha256: str
    artifact_reuse_sha256: str
    dependency_execution_performed: bool
    build_execution_performed: bool
    bundle: SealedReleaseBundle


def _sha256(path: Path, code: str) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as error:
        raise LifecycleBlockedError(code, "reseal binding is unavailable") from error


def _hex(value: str, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and all(character in "0123456789abcdef" for character in value.lower())


def recompose_and_reseal(inputs: ResealInputs) -> ResealResult:
    """Recompose source and copy only already-bound build artifacts; execute nothing."""

    if not _hex(inputs.target_commit_sha, 40) or not _hex(inputs.implementation_commit, 40):
        raise LifecycleBlockedError("reseal_identity_invalid", "reseal commit identity is invalid")
    if not _hex(inputs.official_tree_sha256, 64) or not _hex(inputs.replay_manifest_sha256, 64):
        raise LifecycleBlockedError("reseal_identity_invalid", "reseal source identity is invalid")
    manifest = load_declared_overlay(inputs.overlay_root, inputs.overlay_manifest_path)
    if (manifest.target_tag, manifest.target_commit_sha) != (inputs.target_tag, inputs.target_commit_sha):
        raise LifecycleBlockedError("reseal_overlay_target_mismatch", "overlay target differs from selected official release")
    prior_root = Path(inputs.prior_release_root).resolve()
    prior_manifest = prior_root / "release-manifest.json"
    prior_manifest_sha = _sha256(prior_manifest, "reseal_prior_release_missing")
    if prior_manifest_sha != inputs.expected_prior_manifest_sha256:
        raise LifecycleBlockedError("reseal_prior_release_drift", "prior release manifest changed")
    try:
        prior = json.loads(prior_manifest.read_text(encoding="utf-8"))
        identity = prior["identity"]
        bindings = identity["bindings"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise LifecycleBlockedError("reseal_prior_release_invalid", "prior release manifest is invalid") from error
    if (
        prior.get("status") != "SEALED_CODE_ONLY"
        or identity.get("candidate_id") != inputs.candidate_id
        or identity.get("target_tag") != inputs.target_tag
        or identity.get("target_commit_sha") != inputs.target_commit_sha
        or not isinstance(bindings, dict)
    ):
        raise LifecycleBlockedError("reseal_prior_release_mismatch", "prior release targets a different candidate")
    artifact_ids = tuple(sorted(set(bindings) - {"composed-source"}))
    if not artifact_ids:
        raise LifecycleBlockedError("reseal_prior_artifact_missing", "prior release has no reusable build artifacts")
    artifacts: list[ArtifactBinding] = []
    reuse_binding: dict[str, str] = {}
    for artifact_id in artifact_ids:
        path = prior_root / "artifacts" / artifact_id
        expected = bindings.get(artifact_id, {}).get("tree_sha256")
        if not path.is_dir() or path.is_symlink() or not isinstance(expected, str) or tree_digest(path) != expected:
            raise LifecycleBlockedError("reseal_prior_artifact_drift", "a prior build artifact changed")
        artifacts.append(ArtifactBinding(artifact_id, path))
        reuse_binding[artifact_id] = expected
    candidate = construct_composed_candidate(
        ComposedCandidateInputs(
            official_source=inputs.official_source,
            official_tree_sha256=inputs.official_tree_sha256,
            overlay_root=inputs.overlay_root,
            overlay_manifest=manifest,
            replay_manifest_sha256=inputs.replay_manifest_sha256,
            implementation_commit=inputs.implementation_commit,
            isolated_root=inputs.isolated_root,
            protected_paths=inputs.protected_paths,
        )
    )
    bundle = build_release_bundle(
        ReleaseBundleInputs(
            inputs.candidate_id,
            inputs.target_tag,
            inputs.target_commit_sha,
            candidate.immutable_source,
            tuple(artifacts),
        ),
        Path(inputs.isolated_root) / "releases",
    )
    reuse_sha = hashlib.sha256(
        json.dumps(
            {"prior_manifest_sha256": prior_manifest_sha, "artifacts": reuse_binding},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return ResealResult(
        composition_id=candidate.composition_id,
        composed_tree_sha256=candidate.composed_tree_sha256,
        overlay_manifest_sha256=manifest.digest(),
        reused_artifact_ids=artifact_ids,
        prior_manifest_sha256=prior_manifest_sha,
        artifact_reuse_sha256=reuse_sha,
        dependency_execution_performed=False,
        build_execution_performed=False,
        bundle=bundle,
    )
