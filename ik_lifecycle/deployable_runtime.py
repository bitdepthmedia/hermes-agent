"""Frozen runtime sealing distinct from code-only release evidence."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
from typing import Iterable

from .composed_source import tree_digest
from .models import LifecycleBlockedError


@dataclass(frozen=True)
class RuntimeSurface:
    surface_id: str
    path: Path


@dataclass(frozen=True)
class LockBinding:
    lock_id: str
    path: Path


@dataclass(frozen=True)
class DeployableRuntimeInputs:
    candidate_id: str
    target_tag: str
    target_commit_sha: str
    source: Path
    surfaces: tuple[RuntimeSurface, ...]
    lockfiles: tuple[LockBinding, ...]
    router_config: Path
    model_manifest: Path
    expected_python: tuple[int, int]


@dataclass(frozen=True)
class SealedDeployableRuntime:
    release_id: str
    root: Path
    manifest_path: Path


@dataclass(frozen=True)
class RuntimeValidation:
    status: str
    release_id: str


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _materialized_tree_digest(root: Path) -> str:
    """Digest the tree as copytree(symlinks=False) will materialize it."""

    base = Path(root)
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        relative = path.relative_to(base).as_posix()
        if path.is_symlink():
            try:
                target = path.resolve(strict=True)
            except OSError as exc:
                raise LifecycleBlockedError("runtime_surface_symlink_invalid", "runtime surface symlink is invalid") from exc
            if not target.is_file():
                raise LifecycleBlockedError("runtime_surface_symlink_invalid", "runtime surface symlink must resolve to a file")
            digest.update(f"F\0{relative}\0".encode()); digest.update(target.read_bytes()); digest.update(b"\0")
        elif path.is_file():
            digest.update(f"F\0{relative}\0".encode()); digest.update(path.read_bytes()); digest.update(b"\0")
        elif path.is_dir():
            digest.update(f"D\0{relative}\0".encode())
    return digest.hexdigest()


def _casefold_collisions(root: Path) -> tuple[str, ...]:
    seen: dict[str, str] = {}
    collisions: list[str] = []
    base = Path(root)
    for path in sorted(base.rglob("*"), key=lambda item: item.relative_to(base).as_posix()):
        relative = path.relative_to(base).as_posix()
        folded = relative.casefold()
        prior = seen.setdefault(folded, relative)
        if prior != relative:
            collisions.append(folded)
    return tuple(collisions)


def _filesystem_case_sensitive(path: Path) -> bool:
    ancestor = Path(path)
    while not ancestor.exists():
        ancestor = ancestor.parent
    probe = Path(tempfile.mkdtemp(prefix=".ik-case-proof-", dir=ancestor))
    lower = probe / "case-proof"
    upper = probe / "CASE-PROOF"
    try:
        lower.write_bytes(b"lower")
        if upper.exists():
            return False
        upper.write_bytes(b"upper")
        return lower.read_bytes() != upper.read_bytes()
    finally:
        for item in (lower, upper):
            try:
                item.unlink()
            except FileNotFoundError:
                pass
        probe.rmdir()


def _make_writable(root: Path) -> None:
    if not root.exists():
        return
    root.chmod(stat.S_IMODE(root.stat().st_mode) | 0o700)
    for path in root.rglob("*"):
        if not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) | (0o700 if path.is_dir() else 0o600))


def _validate_inputs(inputs: DeployableRuntimeInputs, output: Path, running_roots: Iterable[Path]) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{40}", inputs.target_commit_sha):
        raise LifecycleBlockedError("runtime_target_invalid", "runtime target commit is invalid")
    source = Path(inputs.source)
    if source.is_symlink() or not source.is_dir():
        raise LifecycleBlockedError("runtime_source_invalid", "runtime source is missing or a symlink")
    if _casefold_collisions(source) and not _filesystem_case_sensitive(output):
        raise LifecycleBlockedError(
            "runtime_case_sensitive_store_required",
            "complete upstream source requires a case-sensitive release store",
        )
    output_resolved = output.resolve(strict=False)
    for running in running_roots:
        running_resolved = Path(running).resolve(strict=False)
        if _is_within(output_resolved, running_resolved) or _is_within(running_resolved, output_resolved):
            raise LifecycleBlockedError("runtime_running_root_overlap", "runtime release root overlaps a running root")
    surfaces: dict[str, dict[str, object]] = {}
    for surface in inputs.surfaces:
        path = Path(surface.path)
        if not surface.surface_id or surface.surface_id in surfaces:
            raise LifecycleBlockedError("runtime_surface_duplicate", "runtime surface identity is invalid")
        if path.is_symlink():
            raise LifecycleBlockedError("runtime_surface_symlink", "runtime surface may not be a symlink")
        resolved = path.resolve(strict=True)
        if not resolved.is_dir():
            raise LifecycleBlockedError("runtime_surface_missing", "runtime surface is missing")
        surfaces[surface.surface_id] = {"tree_sha256": _materialized_tree_digest(resolved)}
    required_surfaces = {"python-runtime", "built-assets", "service-definitions", "model-runtime"}
    if set(surfaces) != required_surfaces:
        raise LifecycleBlockedError("runtime_surface_set_invalid", "deployable runtime surface set is incomplete or unexpected")
    runtime = next((Path(item.path).resolve() for item in inputs.surfaces if item.surface_id == "python-runtime"), None)
    if runtime is None:
        raise LifecycleBlockedError("runtime_python_missing", "python runtime surface is required")
    python = runtime / "bin/python"
    if not python.is_file() or not os.access(python, os.X_OK):
        raise LifecycleBlockedError("runtime_python_not_executable", "python runtime executable is missing or not executable")
    try:
        version = subprocess.run(
            (str(python), "-c", "import json,sys; print(json.dumps(list(sys.version_info[:2])))"),
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"},
        )
        observed_python = tuple(json.loads(version.stdout.strip()))
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, TypeError) as exc:
        raise LifecycleBlockedError("runtime_python_invalid", "runtime Python identity cannot be verified") from exc
    if observed_python != inputs.expected_python:
        raise LifecycleBlockedError("runtime_python_version_mismatch", "runtime Python version does not match the frozen input")
    if not any(runtime.glob("lib/python*/site-packages/*.dist-info/METADATA")):
        raise LifecycleBlockedError("runtime_metadata_missing", "installed runtime metadata is missing")
    model_runtime = next(Path(item.path).resolve() for item in inputs.surfaces if item.surface_id == "model-runtime")
    model_executable = model_runtime / "ollama"
    if not model_executable.is_file() or not os.access(model_executable, os.X_OK):
        raise LifecycleBlockedError("runtime_model_executable_missing", "model runtime executable is missing")
    lockfiles: dict[str, str] = {}
    for binding in inputs.lockfiles:
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,63}", binding.lock_id) or binding.lock_id in lockfiles:
            raise LifecycleBlockedError("runtime_lock_identity_invalid", "runtime lock identity is invalid")
        path = Path(binding.path)
        if path.is_symlink() or not path.is_file():
            raise LifecycleBlockedError("runtime_lock_missing", "runtime committed lock is missing")
        lockfiles[binding.lock_id] = _sha256(path)
    if not {"python-uv", "root-npm"}.issubset(lockfiles):
        raise LifecycleBlockedError("runtime_lock_set_invalid", "runtime lock set is incomplete")
    router = Path(inputs.router_config)
    model = Path(inputs.model_manifest)
    if router.is_symlink() or model.is_symlink() or not router.is_file() or not model.is_file():
        raise LifecycleBlockedError("runtime_config_missing", "router or model manifest is missing")
    return {
        "candidate_id": inputs.candidate_id,
        "target_tag": inputs.target_tag,
        "target_commit_sha": inputs.target_commit_sha,
        "source_tree_sha256": tree_digest(source.resolve()),
        "surfaces": surfaces,
        "lockfiles": lockfiles,
        "router_config_sha256": _sha256(router),
        "model_manifest_sha256": _sha256(model),
        "python": {
            "major": inputs.expected_python[0],
            "minor": inputs.expected_python[1],
            "executable_sha256": _sha256(python),
        },
        "model_runtime_executable_sha256": _sha256(model_executable),
    }


def _read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not path.is_symlink():
            path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def validate_deployable_runtime(root: Path) -> RuntimeValidation:
    release = Path(root).resolve()
    manifest_path = release / "runtime-manifest.json"
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleBlockedError("runtime_manifest_invalid", "runtime artifact manifest is invalid") from exc
    if document.get("status") != "SEALED_DEPLOYABLE_RUNTIME":
        raise LifecycleBlockedError("runtime_status_invalid", "runtime artifact is not deployable")
    identity = document.get("identity", {})
    if tree_digest(release / "source") != identity.get("source_tree_sha256"):
        raise LifecycleBlockedError("runtime_artifact_tampered", "runtime artifact source digest changed")
    for surface_id, binding in identity.get("surfaces", {}).items():
        if tree_digest(release / "surfaces" / surface_id) != binding.get("tree_sha256"):
            raise LifecycleBlockedError("runtime_artifact_tampered", "runtime artifact surface digest changed")
    if _sha256(release / "config/router.json") != identity.get("router_config_sha256"):
        raise LifecycleBlockedError("runtime_artifact_tampered", "runtime artifact router digest changed")
    if _sha256(release / "config/model.json") != identity.get("model_manifest_sha256"):
        raise LifecycleBlockedError("runtime_artifact_tampered", "runtime artifact model digest changed")
    for lock_id, expected in identity.get("lockfiles", {}).items():
        if _sha256(release / "config/locks" / f"{lock_id}.lock") != expected:
            raise LifecycleBlockedError("runtime_artifact_tampered", "runtime artifact lock digest changed")
    if any(path.stat().st_mode & 0o222 for path in (release, *release.rglob("*")) if not path.is_symlink()):
        raise LifecycleBlockedError("runtime_artifact_writable", "runtime artifact contains writable entries")
    expected_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    if document.get("release_id") != expected_id or release.name != expected_id:
        raise LifecycleBlockedError("runtime_identity_invalid", "runtime artifact identity does not match")
    return RuntimeValidation("CLEAR", expected_id)


def seal_deployable_runtime(
    inputs: DeployableRuntimeInputs,
    release_root: Path,
    *,
    running_roots: Iterable[Path],
) -> SealedDeployableRuntime:
    releases = Path(release_root).resolve(strict=False)
    identity = _validate_inputs(inputs, releases, running_roots)
    release_id = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]
    root = releases / release_id
    if root.exists():
        validate_deployable_runtime(root)
        return SealedDeployableRuntime(release_id, root, root / "runtime-manifest.json")
    releases.mkdir(parents=True, exist_ok=True)
    staging = releases / f".{release_id}.{os.getpid()}.staging"
    if staging.exists():
        raise LifecycleBlockedError("runtime_staging_exists", "runtime staging path already exists")
    staging.mkdir(mode=0o700)
    try:
        shutil.copytree(Path(inputs.source).resolve(), staging / "source", symlinks=False)
        for surface in inputs.surfaces:
            shutil.copytree(Path(surface.path).resolve(), staging / "surfaces" / surface.surface_id, symlinks=False)
        (staging / "config").mkdir()
        shutil.copy2(inputs.router_config, staging / "config/router.json")
        shutil.copy2(inputs.model_manifest, staging / "config/model.json")
        (staging / "config/locks").mkdir()
        for binding in inputs.lockfiles:
            shutil.copy2(binding.path, staging / "config/locks" / f"{binding.lock_id}.lock")
        manifest = {
            "schema_id": "ik.hermes.deployable-runtime.v1",
            "status": "SEALED_DEPLOYABLE_RUNTIME",
            "release_id": release_id,
            "identity": identity,
        }
        manifest_path = staging / "runtime-manifest.json"
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        _read_only(staging)
        os.replace(staging, root)
        validate_deployable_runtime(root)
        return SealedDeployableRuntime(release_id, root, root / "runtime-manifest.json")
    except Exception:
        if staging.exists():
            _make_writable(staging)
            failure = releases / f"{release_id}.{os.getpid()}.failed"
            try:
                (staging / "FAILURE").write_text("deployable_runtime_seal_failed\n", encoding="utf-8")
                os.replace(staging, failure)
            except OSError:
                pass
        raise
