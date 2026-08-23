"""Immutable cell-specific deployment pack bound to one shared Hermes release."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

from .composed_source import tree_digest
from .models import LifecycleBlockedError


@dataclass(frozen=True)
class CellDeploymentInputs:
    cell_id: str
    shared_release: Path
    shared_release_manifest_sha256: str
    service_definitions: Path
    compatibility_surface: Path
    router_config: Path
    model_manifest: Path


@dataclass(frozen=True)
class SealedCellDeployment:
    deployment_id: str
    root: Path
    manifest_path: Path


@dataclass(frozen=True)
class CellDeploymentValidation:
    status: str
    deployment_id: str


def _sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        if not path.is_symlink(): path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def _shared_release(path: Path, expected_sha256: str) -> dict[str, object]:
    root = Path(path).resolve()
    manifest = root / "runtime-manifest.json"
    try: document = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleBlockedError("cell_shared_release_invalid", "shared release is invalid") from exc
    if _sha(manifest) != expected_sha256 or document.get("status") != "SEALED_DEPLOYABLE_RUNTIME" or document.get("release_id") != root.name:
        raise LifecycleBlockedError("cell_shared_release_drift", "shared release binding changed")
    return document


def build_cell_deployment(inputs: CellDeploymentInputs, deployment_root: Path) -> SealedCellDeployment:
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", inputs.cell_id):
        raise LifecycleBlockedError("cell_deployment_identity_invalid", "cell identity is invalid")
    shared = Path(inputs.shared_release).resolve()
    _shared_release(shared, inputs.shared_release_manifest_sha256)
    services = Path(inputs.service_definitions).resolve()
    compatibility = Path(inputs.compatibility_surface).resolve()
    router = Path(inputs.router_config).resolve()
    model = Path(inputs.model_manifest).resolve()
    if any(path.is_symlink() for path in (services, compatibility, router, model)) or not services.is_dir() or not compatibility.is_dir() or not router.is_file() or not model.is_file():
        raise LifecycleBlockedError("cell_deployment_surface_invalid", "cell deployment surface is invalid")
    identity = {
        "cell_id": inputs.cell_id,
        "shared_release_id": shared.name,
        "shared_release_path_sha256": hashlib.sha256(str(shared).encode()).hexdigest(),
        "shared_release_manifest_sha256": inputs.shared_release_manifest_sha256,
        "service_definitions_tree_sha256": tree_digest(services),
        "compatibility_surface_tree_sha256": tree_digest(compatibility),
        "router_config_sha256": _sha(router),
        "model_manifest_sha256": _sha(model),
    }
    deployment_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    output = Path(deployment_root).resolve(strict=False) / deployment_id
    if output.exists():
        validation = validate_cell_deployment(output)
        if validation.deployment_id != deployment_id: raise LifecycleBlockedError("cell_deployment_identity_invalid", "cell deployment identity changed")
        return SealedCellDeployment(deployment_id, output, output / "deployment-manifest.json")
    staging = output.with_name(f".{deployment_id}.{os.getpid()}.staging")
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        staging.mkdir(mode=0o700)
        shutil.copytree(services, staging / "services", symlinks=False)
        shutil.copytree(compatibility, staging / "compatibility", symlinks=False)
        (staging / "config").mkdir()
        shutil.copy2(router, staging / "config/router.json")
        shutil.copy2(model, staging / "config/model.json")
        (staging / "shared-release.path").write_text(str(shared) + "\n", encoding="utf-8")
        document = {"schema_id": "ik.hermes.cell-deployment.v1", "status": "SEALED_CELL_DEPLOYMENT", "deployment_id": deployment_id, "identity": identity}
        (staging / "deployment-manifest.json").write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        _read_only(staging)
        os.replace(staging, output)
        validate_cell_deployment(output)
        return SealedCellDeployment(deployment_id, output, output / "deployment-manifest.json")
    except Exception:
        if staging.exists():
            failed = staging.with_name(staging.name + ".failed")
            try: os.replace(staging, failed)
            except OSError: pass
        raise


def validate_cell_deployment(root: Path) -> CellDeploymentValidation:
    output = Path(root).resolve()
    manifest = output / "deployment-manifest.json"
    try: document = json.loads(manifest.read_text(encoding="utf-8")); shared = Path((output / "shared-release.path").read_text(encoding="utf-8").strip()).resolve()
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleBlockedError("cell_deployment_manifest_invalid", "cell deployment manifest is invalid") from exc
    identity = document.get("identity", {})
    expected_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    if document.get("status") != "SEALED_CELL_DEPLOYMENT" or document.get("deployment_id") != expected_id or output.name != expected_id:
        raise LifecycleBlockedError("cell_deployment_identity_invalid", "cell deployment identity changed")
    if hashlib.sha256(str(shared).encode()).hexdigest() != identity.get("shared_release_path_sha256"):
        raise LifecycleBlockedError("cell_deployment_tampered", "cell deployment shared release changed")
    _shared_release(shared, str(identity.get("shared_release_manifest_sha256", "")))
    checks = (
        (tree_digest(output / "services"), identity.get("service_definitions_tree_sha256")),
        (tree_digest(output / "compatibility"), identity.get("compatibility_surface_tree_sha256")),
        (_sha(output / "config/router.json"), identity.get("router_config_sha256")),
        (_sha(output / "config/model.json"), identity.get("model_manifest_sha256")),
    )
    if any(observed != expected for observed, expected in checks):
        raise LifecycleBlockedError("cell_deployment_tampered", "cell deployment surface was tampered")
    if any(path.stat().st_mode & 0o222 for path in (output, *output.rglob("*")) if not path.is_symlink()):
        raise LifecycleBlockedError("cell_deployment_writable", "cell deployment contains writable entries")
    return CellDeploymentValidation("CLEAR", expected_id)
