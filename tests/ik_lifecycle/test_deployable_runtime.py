from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import stat
import sys
from unittest.mock import patch

import pytest

from ik_lifecycle.deployable_runtime import (
    DeployableRuntimeInputs,
    LockBinding,
    RuntimeSurface,
    seal_deployable_runtime,
    validate_deployable_runtime,
)
from ik_lifecycle.models import LifecycleBlockedError


def _write(path: Path, value: bytes, mode: int = 0o644) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    path.chmod(mode)
    return hashlib.sha256(value).hexdigest()


def _inputs(root: Path) -> DeployableRuntimeInputs:
    source = root / "source"
    runtime = root / "runtime"
    assets = root / "assets"
    router = root / "router.json"
    services = root / "services"
    model_runtime = root / "model-runtime"
    model = root / "model.json"
    _write(source / "run_agent.py", b"print('hermes')\n")
    (runtime / "bin").mkdir(parents=True)
    shutil.copy2(sys.executable, runtime / "bin/python")
    (runtime / "bin/python").chmod(0o755)
    _write(runtime / "lib/python3.11/site-packages/locked.dist-info/METADATA", b"Name: locked\nVersion: 1.0\n")
    _write(assets / "ui/index.js", b"built")
    _write(router, b'{"primary":"qwen38-27b-q4km","reasoning":"capability-aware"}\n')
    _write(services / "com.ik.hermes-ernie.plist", b"fixture-service")
    _write(services / "service-manifest.json", b'{"status":"CLEAR_EXACT_SERVICE_DEFINITIONS"}\n')
    _write(model_runtime / "ollama", b"#!/bin/sh\nexit 0\n", 0o755)
    uv_lock = source / "uv.lock"; _write(uv_lock, b"version = 1\n")
    package_lock = source / "package-lock.json"; _write(package_lock, b'{"lockfileVersion":3}\n')
    _write(model, b'{"model_sha256":"31629f","projector_sha256":"2e968a"}\n')
    return DeployableRuntimeInputs(
        candidate_id="candidate-1",
        target_tag="v2026.8.18",
        target_commit_sha="e624e9fde561e1add9388384012b295fde669ade",
        source=source,
        surfaces=(
            RuntimeSurface("python-runtime", runtime),
            RuntimeSurface("built-assets", assets),
            RuntimeSurface("service-definitions", services),
            RuntimeSurface("model-runtime", model_runtime),
        ),
        lockfiles=(LockBinding("python-uv", uv_lock), LockBinding("root-npm", package_lock)),
        router_config=router,
        model_manifest=model,
        expected_python=sys.version_info[:2],
    )


def test_deployable_seal_binds_runtime_router_services_and_model(tmp_path: Path) -> None:
    sealed = seal_deployable_runtime(_inputs(tmp_path), tmp_path / "releases", running_roots=())
    document = json.loads(sealed.manifest_path.read_text(encoding="utf-8"))

    assert document["status"] == "SEALED_DEPLOYABLE_RUNTIME"
    assert document["identity"]["target_commit_sha"] == "e624e9fde561e1add9388384012b295fde669ade"
    assert set(document["identity"]["surfaces"]) == {
        "built-assets",
        "python-runtime",
        "service-definitions",
        "model-runtime",
    }
    assert set(document["identity"]["lockfiles"]) == {"python-uv", "root-npm"}
    assert document["identity"]["router_config_sha256"]
    assert document["identity"]["model_manifest_sha256"]
    assert not (sealed.root.stat().st_mode & stat.S_IWUSR)
    assert validate_deployable_runtime(sealed.root).status == "CLEAR"


def test_runtime_seal_is_idempotent_and_tamper_fails_closed(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    first = seal_deployable_runtime(inputs, tmp_path / "releases", running_roots=())
    second = seal_deployable_runtime(inputs, tmp_path / "releases", running_roots=())
    assert first.root == second.root

    runtime_file = first.root / "surfaces/python-runtime/bin/python"
    runtime_file.chmod(0o755)
    runtime_file.write_text("tampered", encoding="utf-8")
    runtime_file.chmod(0o555)
    with pytest.raises(LifecycleBlockedError, match="runtime artifact"):
        validate_deployable_runtime(first.root)


def test_runtime_seal_rejects_running_root_symlink_and_non_executable_python(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    with pytest.raises(LifecycleBlockedError, match="running"):
        seal_deployable_runtime(inputs, tmp_path / "source/releases", running_roots=(tmp_path / "source",))

    link = tmp_path / "runtime-link"
    link.symlink_to(inputs.surfaces[0].path, target_is_directory=True)
    linked = DeployableRuntimeInputs(
        **{**inputs.__dict__, "surfaces": (RuntimeSurface("python-runtime", link), *inputs.surfaces[1:])}
    )
    with pytest.raises(LifecycleBlockedError, match="symlink"):
        seal_deployable_runtime(linked, tmp_path / "linked-releases", running_roots=())

    python = inputs.surfaces[0].path / "bin/python"
    python.chmod(0o644)
    with pytest.raises(LifecycleBlockedError, match="executable"):
        seal_deployable_runtime(inputs, tmp_path / "bad-releases", running_roots=())


def test_runtime_seal_materializes_safe_inner_executable_symlink(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    runtime = inputs.surfaces[0].path
    target = runtime / "bin/python3.11"
    target.write_bytes((runtime / "bin/python").read_bytes())
    target.chmod(0o755)
    (runtime / "bin/python").unlink()
    (runtime / "bin/python").symlink_to("python3.11")

    sealed = seal_deployable_runtime(inputs, tmp_path / "releases", running_roots=())
    materialized = sealed.root / "surfaces/python-runtime/bin/python"
    assert materialized.is_file() and not materialized.is_symlink()
    assert validate_deployable_runtime(sealed.root).status == "CLEAR"


def test_runtime_seal_requires_every_deployment_surface_and_committed_lock(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    missing = DeployableRuntimeInputs(**{**inputs.__dict__, "surfaces": inputs.surfaces[:-1]})
    with pytest.raises(LifecycleBlockedError, match="surface"):
        seal_deployable_runtime(missing, tmp_path / "missing-surface", running_roots=())

    bad_lock = inputs.lockfiles[0].path
    bad_lock.unlink()
    with pytest.raises(LifecycleBlockedError, match="lock"):
        seal_deployable_runtime(inputs, tmp_path / "missing-lock", running_roots=())


def test_casefold_collision_requires_case_sensitive_release_store(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    with patch("ik_lifecycle.deployable_runtime._casefold_collisions", return_value=("x",)), patch(
        "ik_lifecycle.deployable_runtime._filesystem_case_sensitive", return_value=False
    ):
        with pytest.raises(LifecycleBlockedError, match="case-sensitive"):
            seal_deployable_runtime(inputs, tmp_path / "releases", running_roots=())


def test_failed_seal_retains_staging_evidence(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    original = shutil.copytree
    calls = 0

    def fail_second(source: Path, destination: Path, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            Path(destination).mkdir(parents=True)
            (Path(destination) / "partial").write_text("retained", encoding="utf-8")
            raise OSError("injected copy failure")
        return original(source, destination, **kwargs)

    with patch("ik_lifecycle.deployable_runtime.shutil.copytree", side_effect=fail_second):
        with pytest.raises(OSError, match="injected"):
            seal_deployable_runtime(inputs, tmp_path / "releases", running_roots=())
    failures = tuple((tmp_path / "releases").glob("*.failed"))
    assert len(failures) == 1
    assert (failures[0] / "FAILURE").is_file()
