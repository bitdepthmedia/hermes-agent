"""Non-secret profile inventory for approved clones and synthetic fixtures."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InventoryPolicy:
    excluded_names: tuple[str, ...] = (".env", "credentials.json", "secrets.json")


@dataclass(frozen=True)
class InventoryEntry:
    path: str
    role: str
    size: int
    mode: int
    sha256: str


@dataclass(frozen=True)
class ProfileInventory:
    root: str
    entries: tuple[InventoryEntry, ...]
    excluded_paths: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_role": "profile-home",
            "entries": [entry.__dict__ for entry in self.entries],
            "excluded_paths": list(self.excluded_paths),
        }


def inventory_profile(home: Path, policy: InventoryPolicy) -> ProfileInventory:
    root = Path(home).resolve()
    entries: list[InventoryEntry] = []
    excluded: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if path.name in policy.excluded_names or any(part in {"credentials", "secrets"} for part in path.parts):
            excluded.append(relative)
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        role = "database" if path.suffix in {".sqlite", ".db"} else "continuity-file"
        entries.append(InventoryEntry(relative, role, path.stat().st_size, path.stat().st_mode & 0o777, digest))
    return ProfileInventory(str(root), tuple(entries), tuple(excluded))
