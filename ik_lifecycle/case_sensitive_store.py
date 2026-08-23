"""Fail-closed local case-sensitive release-store lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import os
import stat
import subprocess
from typing import Callable

from .deployable_runtime import _filesystem_case_sensitive
from .models import LifecycleBlockedError


@dataclass(frozen=True)
class StoreCommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class CaseSensitiveStoreReceipt:
    status: str
    cell_root_sha256: str
    image_path_sha256: str
    mount_path_sha256: str
    command_sha256: str
    size_gib: int


def _run(argv: tuple[str, ...]) -> StoreCommandResult:
    completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=180)
    return StoreCommandResult(completed.returncode, completed.stdout, completed.stderr)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _harden_permissions(root: Path) -> None:
    os.chmod(root, 0o700)
    for path in root.rglob("*"):
        if path.is_symlink():
            raise LifecycleBlockedError("release_store_symlink", "release store contains a symlink")
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    if stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise LifecycleBlockedError("release_store_permissions", "release store permissions are not restrictive")


class CaseSensitiveReleaseStore:
    def __init__(
        self,
        cell_root: Path,
        image_path: Path,
        mount_path: Path,
        *,
        runner: Callable[[tuple[str, ...]], StoreCommandResult] = _run,
        case_sensitive_probe: Callable[[Path], bool] = _filesystem_case_sensitive,
        materialized_probe: Callable[[Path, Path], bool] | None = None,
        permission_hardener: Callable[[Path], None] = _harden_permissions,
    ) -> None:
        self.cell_root = Path(cell_root).resolve(strict=False)
        self.image_path = Path(image_path).resolve(strict=False)
        self.mount_path = Path(mount_path).resolve(strict=False)
        if not _inside(self.image_path, self.cell_root) or not _inside(self.mount_path, self.cell_root):
            raise LifecycleBlockedError("release_store_scope_invalid", "release store must remain inside its cell root")
        if self.image_path.is_symlink() or self.mount_path.is_symlink():
            raise LifecycleBlockedError("release_store_symlink", "release store paths may not be symlinks")
        self.runner = runner
        self.case_sensitive_probe = case_sensitive_probe
        self.materialized_probe = materialized_probe or (lambda image, mount: image.is_dir() and mount.is_dir())
        self.permission_hardener = permission_hardener

    def create_and_mount(self, *, size_gib: int, volume_name: str) -> CaseSensitiveStoreReceipt:
        if not 4 <= size_gib <= 128 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{2,63}", volume_name):
            raise LifecycleBlockedError("release_store_spec_invalid", "release store specification is invalid")
        if self.image_path.exists() or self.mount_path.exists():
            raise LifecycleBlockedError("release_store_exists", "release store target already exists")
        self.cell_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        create = (
            "/usr/bin/hdiutil", "create", "-type", "SPARSEBUNDLE", "-fs", "Case-sensitive APFS", "-size", f"{size_gib}g",
            "-volname", volume_name, str(self.image_path),
        )
        mount = (
            "/usr/bin/hdiutil", "attach", "-nobrowse", "-mountpoint", str(self.mount_path), str(self.image_path),
        )
        commands = (create, mount)
        for argv in commands:
            result = self.runner(argv)
            if result.returncode != 0:
                raise LifecycleBlockedError("release_store_command_failed", "release store command failed")
        if not self.materialized_probe(self.image_path, self.mount_path):
            raise LifecycleBlockedError("release_store_materialization_failed", "release store did not materialize")
        self.permission_hardener(self.image_path)
        if not self.case_sensitive_probe(self.mount_path):
            raise LifecycleBlockedError("release_store_case_invalid", "release store is not case-sensitive")
        digest = hashlib.sha256("\0".join("\0".join(argv) for argv in commands).encode()).hexdigest()
        return CaseSensitiveStoreReceipt(
            "CLEAR_CASE_SENSITIVE_RELEASE_STORE",
            hashlib.sha256(str(self.cell_root).encode()).hexdigest(),
            hashlib.sha256(str(self.image_path).encode()).hexdigest(),
            hashlib.sha256(str(self.mount_path).encode()).hexdigest(),
            digest,
            size_gib,
        )
