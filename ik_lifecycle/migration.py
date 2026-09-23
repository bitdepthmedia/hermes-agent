"""Destination-clone-only continuity migration primitives."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil

from .profile_inventory import InventoryPolicy, inventory_profile
from .sqlite_backup import online_backup


@dataclass(frozen=True)
class MigratedProfile:
    source_root: Path
    destination_root: Path
    migrated_files: tuple[str, ...]


def migrate_profile(source: Path, destination: Path) -> MigratedProfile:
    source_root = Path(source).resolve()
    destination_root = Path(destination).resolve()
    if source_root == destination_root or source_root in destination_root.parents:
        raise ValueError("migration must target an independent clone")
    if destination_root.exists():
        raise ValueError("migration destination must be new")
    destination_root.mkdir(parents=True)
    inventory = inventory_profile(source_root, InventoryPolicy())
    migrated: list[str] = []
    for entry in inventory.entries:
        src = source_root / entry.path
        dst = destination_root / entry.path
        dst.parent.mkdir(parents=True, exist_ok=True)
        if entry.role == "database":
            online_backup(src, dst)
        else:
            shutil.copy2(src, dst)
        migrated.append(entry.path)
    return MigratedProfile(source_root, destination_root, tuple(migrated))
