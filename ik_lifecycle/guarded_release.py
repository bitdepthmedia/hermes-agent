"""Digest-approved operator entrypoints; no installs, lifecycle scripts, or SSH."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import socket

from .deployable_runtime import (
    DeployableRuntimeInputs,
    LockBinding,
    RuntimeSurface,
    seal_deployable_runtime,
    validate_deployable_runtime,
)
from .models import LifecycleBlockedError
from .runtime_imports import validate_runtime_imports, validate_runtime_tools
from .source_provenance import SourceProvenance, verify_source_provenance


def review_subject(plan: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            {k: v for k, v in plan.items() if k != "reviews"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def read_approved_plan(path: Path, approval: str, operation: str) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != approval:
        raise LifecycleBlockedError(
            "release_plan_approval", "approval must bind the exact release plan bytes"
        )
    plan = json.loads(raw)
    if not isinstance(plan, dict) or not isinstance(plan.get("expires_at"), str):
        raise LifecycleBlockedError(
            "release_plan_invalid", "release plan must be an object with an expiry"
        )
    expires = datetime.fromisoformat(plan["expires_at"].replace("Z", "+00:00"))
    if (
        plan.get("schema_id") != "ik.hermes.guarded-release.v1"
        or plan.get("operation") != operation
        or not plan.get("authority")
        or expires.tzinfo is None
        or not 0 < (expires - datetime.now(timezone.utc)).total_seconds() <= 86400
    ):
        raise LifecycleBlockedError(
            "release_plan_invalid",
            "release plan scope, authority, or expiry is invalid",
        )
    reviews = plan.get("reviews", {})
    for kind in ("supply_chain", "privacy"):
        binding = reviews.get(kind) if isinstance(reviews, dict) else None
        if not isinstance(binding, dict):
            raise LifecycleBlockedError(
                "release_review_missing",
                "separate supply-chain and privacy review evidence is required",
            )
        evidence = Path(binding["path"]).read_bytes()
        review = json.loads(evidence)
        if (
            hashlib.sha256(evidence).hexdigest() != binding["sha256"]
            or not isinstance(review, dict)
            or review.get("status") != "CLEAR"
            or not review.get("authority")
            or review.get("subject_sha256") != review_subject(plan)
        ):
            raise LifecycleBlockedError(
                "release_review_invalid",
                "review evidence changed or is not approved CLEAR",
            )
    return plan


def provenance_from_plan(plan: dict) -> SourceProvenance:
    return SourceProvenance(
        Path(plan["repository"]),
        plan["implementation_commit"],
        plan.get("overlay_manifest"),
    )


def verify_artifact(
    release: Path, manifest_sha256: str, provenance: SourceProvenance
) -> dict:
    release = release.resolve(strict=True)
    raw = (release / "runtime-manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != manifest_sha256:
        raise LifecycleBlockedError(
            "release_manifest_binding", "release manifest differs from approved digest"
        )
    validate_deployable_runtime(release)
    document = json.loads(raw)
    identity = document["identity"]
    actual = verify_source_provenance(
        release / "source",
        identity["target_commit_sha"],
        identity["target_tag"],
        provenance,
    )
    if identity.get("provenance") != actual:
        raise LifecycleBlockedError(
            "release_provenance_binding", "release lacks matching sealed provenance"
        )
    python = release / "surfaces/python-runtime/bin/python"
    validate_runtime_imports(python, release / "source")
    validate_runtime_tools(python, release / "source")
    validate_deployable_runtime(release)
    if (release / "runtime-manifest.json").read_bytes() != raw:
        raise LifecycleBlockedError(
            "release_manifest_binding", "release manifest changed during verification"
        )
    return {
        "status": "CLEAR",
        "release_id": document["release_id"],
        "manifest_sha256": manifest_sha256,
        "source_tree_sha256": identity["source_tree_sha256"],
        "target_commit_sha": identity["target_commit_sha"],
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "host": socket.gethostname(),
    }


def seal_plan(plan: dict, selection) -> dict:
    inputs = plan["inputs"]
    if (inputs["target_tag"], inputs["target_commit_sha"]) != (
        selection.target.tag,
        selection.target.commit_sha,
    ):
        raise LifecycleBlockedError(
            "release_selection_changed",
            "plan no longer targets one stable release behind",
        )
    if not plan.get("protected_roots"):
        raise LifecycleBlockedError(
            "release_protected_roots", "plan must declare protected runtime roots"
        )
    runtime_inputs = DeployableRuntimeInputs(
        candidate_id=inputs["candidate_id"],
        target_tag=inputs["target_tag"],
        target_commit_sha=inputs["target_commit_sha"],
        source=Path(inputs["source"]),
        surfaces=tuple(
            RuntimeSurface(k, Path(v)) for k, v in inputs["surfaces"].items()
        ),
        lockfiles=tuple(
            LockBinding(k, Path(v)) for k, v in inputs["lockfiles"].items()
        ),
        router_config=Path(inputs["router_config"]),
        model_manifest=Path(inputs["model_manifest"]),
        expected_python=tuple(inputs["expected_python"]),
        provenance=provenance_from_plan(plan["provenance"]),
    )
    sealed = seal_deployable_runtime(
        runtime_inputs,
        Path(plan["release_root"]),
        running_roots=tuple(Path(p) for p in plan["protected_roots"]),
    )
    return {
        "status": "CLEAR",
        "release_id": sealed.release_id,
        "release": str(sealed.root),
        "manifest_sha256": hashlib.sha256(
            sealed.manifest_path.read_bytes()
        ).hexdigest(),
    }
