"""Copy already-audited dependency trees without resolving or executing them."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil

from .models import LifecycleBlockedError


FORBIDDEN_INSTALLED = {
    ("axios", "1.14.1"),
    ("axios", "0.30.4"),
    ("plain-crypto-js", "4.2.1"),
}


def _relative(value: str) -> Path:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.name != "node_modules":
        raise LifecycleBlockedError("audited_dependency_path_invalid", "dependency surface must be a relative node_modules path")
    return Path(*path.parts)


def _validate_symlinks(root: Path, confinement: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        target = os.readlink(path)
        if os.path.isabs(target):
            raise LifecycleBlockedError("audited_dependency_symlink_invalid", "audited dependency has an absolute symlink")
        try:
            (path.parent / target).resolve().relative_to(confinement.resolve())
        except ValueError as exc:
            raise LifecycleBlockedError("audited_dependency_symlink_invalid", "audited dependency symlink escapes its surface") from exc


def dependency_tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(f"L\0{relative}\0{os.readlink(path)}\0".encode())
        elif path.is_file():
            digest.update(f"F\0{relative}\0".encode()); digest.update(path.read_bytes()); digest.update(b"\0")
        elif path.is_dir():
            digest.update(f"D\0{relative}\0".encode())
    return digest.hexdigest()


def _screen_installed_metadata(root: Path) -> None:
    for path in root.rglob("package.json"):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LifecycleBlockedError("audited_dependency_metadata_invalid", "installed package metadata is unreadable") from exc
        if (document.get("name"), document.get("version")) in FORBIDDEN_INSTALLED:
            raise LifecycleBlockedError(
                "audited_dependency_forbidden_version",
                "forbidden installed dependency implementation evidence found",
            )


@dataclass(frozen=True)
class AuditedDependencyCopy:
    status: str
    surfaces: tuple[tuple[str, str], ...]
    digest: str


def materialize_audited_dependencies(
    audit_source: Path,
    build_root: Path,
    surfaces: tuple[str, ...],
) -> AuditedDependencyCopy:
    """Copy exact audited dependency surfaces; never invoke a package manager."""

    audit = Path(audit_source).resolve()
    build = Path(build_root).resolve()
    build.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, str]] = []
    for raw in surfaces:
        relative = _relative(raw)
        source = audit / relative
        destination = build / relative
        if not source.is_dir() or source.is_symlink():
            raise LifecycleBlockedError("audited_dependency_surface_missing", f"audited dependency surface is missing: {raw}")
        _validate_symlinks(source, audit)
        _screen_installed_metadata(source)
        source_digest = dependency_tree_digest(source)
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir() or dependency_tree_digest(destination) != source_digest:
                raise LifecycleBlockedError("audited_dependency_copy_tampered", f"materialized dependency surface changed: {raw}")
            results.append((relative.as_posix(), source_digest))
            continue
        staging_parent = build.parent / ".dependency-staging"
        staging = staging_parent / f"{relative.as_posix().replace('/', '__')}.{os.getpid()}.staging"
        if staging.exists():
            raise LifecycleBlockedError("audited_dependency_staging_exists", "dependency staging path already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging_parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, staging, symlinks=True)
        _validate_symlinks(staging, staging)
        if dependency_tree_digest(staging) != source_digest:
            raise LifecycleBlockedError("audited_dependency_copy_mismatch", "copied dependency surface differs from audit evidence")
        os.replace(staging, destination)
        _validate_symlinks(destination, build)
        try:
            staging_parent.rmdir()
        except OSError:
            pass
        results.append((relative.as_posix(), source_digest))
    payload = json.dumps(results, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return AuditedDependencyCopy("CLEAR", tuple(results), hashlib.sha256(payload).hexdigest())
