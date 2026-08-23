from __future__ import annotations

import hashlib
import json
from pathlib import Path
import stat

import pytest

from ik_lifecycle.cell_deployment import CellDeploymentInputs, build_cell_deployment, validate_cell_deployment
from ik_lifecycle.models import LifecycleBlockedError


def _write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def _inputs(tmp_path: Path) -> CellDeploymentInputs:
    shared = tmp_path / "shared/abc"
    _write(shared / "runtime-manifest.json", b'{"status":"SEALED_DEPLOYABLE_RUNTIME","release_id":"abc"}\n')
    services = tmp_path / "services"
    _write(services / "service-manifest.json", b'{"status":"CLEAR_EXACT_ERNIE_TOPOLOGY"}\n')
    _write(services / "ernie.plist", b"plist")
    compatibility = tmp_path / "compatibility"
    _write(compatibility / "app.py", b"app = object()\n")
    _write(compatibility / "requirements.txt", b"fastapi==0.115.12\n")
    return CellDeploymentInputs(
        cell_id="ernie",
        shared_release=shared,
        shared_release_manifest_sha256=hashlib.sha256((shared / "runtime-manifest.json").read_bytes()).hexdigest(),
        service_definitions=services,
        compatibility_surface=compatibility,
        router_config=Path(__file__).parents[2] / "ik_cells/ernie-router.json",
        model_manifest=Path(__file__).parents[2] / "ik_cells/ernie-model.json",
    )


def test_cell_deployment_binds_shared_release_and_cell_specific_surfaces(tmp_path: Path) -> None:
    result = build_cell_deployment(_inputs(tmp_path), tmp_path / "deployments")
    document = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert document["status"] == "SEALED_CELL_DEPLOYMENT"
    assert document["identity"]["cell_id"] == "ernie"
    assert document["identity"]["shared_release_manifest_sha256"]
    assert document["identity"]["service_definitions_tree_sha256"]
    assert document["identity"]["compatibility_surface_tree_sha256"]
    assert validate_cell_deployment(result.root).status == "CLEAR"
    assert not (result.root.stat().st_mode & stat.S_IWUSR)


def test_cell_deployment_tamper_or_shared_manifest_drift_fails_closed(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    inputs.shared_release.joinpath("runtime-manifest.json").write_bytes(b"drift")
    with pytest.raises(LifecycleBlockedError, match="shared release"):
        build_cell_deployment(inputs, tmp_path / "drift")

    inputs = _inputs(tmp_path / "two")
    result = build_cell_deployment(inputs, tmp_path / "deployments")
    app = result.root / "compatibility/app.py"
    app.chmod(0o600)
    app.write_bytes(b"tamper")
    app.chmod(0o400)
    with pytest.raises(LifecycleBlockedError, match="tampered"):
        validate_cell_deployment(result.root)
