"""Fail-closed filesystem primitives for immutable Hermes cell releases."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .models import LifecycleBlockedError


_CELL_ID = re.compile(r"^(?:ernie|bert)$")


@dataclass(frozen=True)
class CellLayout:
    platform_root: Path
    cell_root: Path
    candidates: Path
    releases: Path
    profiles: Path
    backups: Path
    receipts: Path
    state: Path


@dataclass(frozen=True)
class RollbackPair:
    release: Path
    profile: Path


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _overlap(left: Path, right: Path) -> bool:
    left = _absolute(left).resolve(strict=False)
    right = _absolute(right).resolve(strict=False)
    return left == right or left in right.parents or right in left.parents


def ensure_outside_protected(path: Path, protected_paths: tuple[Path, ...]) -> None:
    """Reject a path that is or contains a protected running-service path."""

    for protected in protected_paths:
        if _overlap(path, protected):
            raise LifecycleBlockedError(
                "running_path_overlap",
                f"Lifecycle path overlaps protected running path: {path}",
            )


def _reject_symlink_components(path: Path, boundary: Path) -> None:
    boundary = _absolute(boundary)
    current = boundary
    if current.is_symlink():
        raise LifecycleBlockedError("symlink_escape", f"Lifecycle root is a symlink: {current}")
    relative = _absolute(path).relative_to(boundary)
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            try:
                target = current.resolve(strict=True)
            except OSError as exc:
                raise LifecycleBlockedError("symlink_escape", f"Broken lifecycle symlink: {current}") from exc
            if target != boundary and boundary not in target.parents:
                raise LifecycleBlockedError("symlink_escape", f"Lifecycle symlink escapes platform root: {current}")
            raise LifecycleBlockedError("symlink_escape", f"Lifecycle directory cannot be a symlink: {current}")


def _reject_symlink_ancestors(path: Path) -> None:
    for component in (*reversed(_absolute(path).parents), _absolute(path)):
        if component.is_symlink():
            raise LifecycleBlockedError("symlink_escape", f"Lifecycle path has a symlink ancestor: {component}")


def prepare_cell_layout(
    platform_root: Path,
    cell_id: str,
    *,
    protected_paths: tuple[Path, ...] = (),
) -> CellLayout:
    """Create independent non-secret storage roots for one lifecycle cell."""

    if not _CELL_ID.fullmatch(cell_id):
        raise LifecycleBlockedError("invalid_cell_id", f"Invalid lifecycle cell id: {cell_id}")
    platform = _absolute(Path(platform_root))
    if platform == Path(platform.anchor) or platform == Path.home().resolve():
        raise LifecycleBlockedError("unsafe_platform_root", f"Unsafe lifecycle platform root: {platform}")
    _reject_symlink_ancestors(platform)
    cell_root = platform / "cells" / cell_id
    layout = CellLayout(
        platform_root=platform,
        cell_root=cell_root,
        candidates=cell_root / "candidates",
        releases=cell_root / "releases",
        profiles=cell_root / "profiles",
        backups=cell_root / "backups",
        receipts=cell_root / "receipts",
        state=cell_root / "state",
    )
    for path in (
        platform,
        platform / "cells",
        layout.cell_root,
        layout.candidates,
        layout.releases,
        layout.profiles,
        layout.backups,
        layout.receipts,
        layout.state,
    ):
        ensure_outside_protected(path, protected_paths)
        _reject_symlink_components(path, platform)
    for path in (
        layout.candidates,
        layout.releases,
        layout.profiles,
        layout.backups,
        layout.receipts,
        layout.state,
    ):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.is_dir() or path.is_symlink():
            raise LifecycleBlockedError("invalid_layout_path", f"Lifecycle path is not a real directory: {path}")
    return layout


def verify_tree_read_only(root: Path) -> None:
    """Prove a sealed tree has no owner/group/other write bit."""

    root = Path(root)
    if not root.is_dir() or root.is_symlink():
        raise LifecycleBlockedError("sealed_release_invalid", f"Sealed release is not a directory: {root}")
    for path in (root, *sorted(root.rglob("*"))):
        if path.is_symlink():
            raise LifecycleBlockedError("sealed_release_symlink", f"Sealed release contains a symlink: {path}")
        if path.stat().st_mode & 0o222:
            raise LifecycleBlockedError("sealed_release_writable", f"Sealed release remains writable: {path}")


def _validate_pointer(pointer: Path, allowed_root: Path) -> Path:
    if not pointer.is_symlink():
        raise LifecycleBlockedError("rollback_prerequisite_missing", f"Rollback pointer is missing: {pointer}")
    try:
        target = pointer.resolve(strict=True)
    except OSError as exc:
        raise LifecycleBlockedError("rollback_prerequisite_missing", f"Rollback pointer is invalid: {pointer}") from exc
    allowed = allowed_root.resolve()
    if target == allowed or allowed not in target.parents or not target.is_dir():
        raise LifecycleBlockedError("rollback_prerequisite_missing", f"Rollback pointer leaves its cell root: {pointer}")
    return target


def validate_rollback_pair(layout: CellLayout, release_pointer: Path, profile_pointer: Path) -> RollbackPair:
    """Require a matched release/profile rollback pair inside one cell."""

    return RollbackPair(
        release=_validate_pointer(Path(release_pointer), layout.releases),
        profile=_validate_pointer(Path(profile_pointer), layout.profiles),
    )
