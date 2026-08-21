"""Deterministic official-source plus declared-overlay composition."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat

from .models import LifecycleBlockedError


@dataclass(frozen=True)
class OverlayManifest:
    target_tag: str
    target_commit_sha: str
    entries: tuple[tuple[str, str], ...]
    source_digest: str = ""

    def digest(self) -> str:
        document = {"target_tag": self.target_tag, "target_commit_sha": self.target_commit_sha, "entries": [list(item) for item in self.entries], "source_digest": self.source_digest}
        return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class ComposedSource:
    root: Path
    tree_digest: str
    overlay_digest: str
    target_tag: str
    target_commit_sha: str


def load_declared_overlay(repo_root: Path, manifest_path: Path) -> OverlayManifest:
    """Expand reviewed overlay roots into a stable, explicit path mapping."""

    root = Path(repo_root).resolve()
    path = Path(manifest_path).resolve()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleBlockedError("overlay_manifest_invalid", "declared overlay manifest is unreadable") from exc
    target = document.get("target", {})
    roots = document.get("roots")
    if (
        document.get("schema_id") != "ik.hermes.extension-overlay-manifest.v1"
        or document.get("core_patch_count") != 0
        or not isinstance(roots, list)
        or not roots
        or not isinstance(target.get("tag"), str)
        or not isinstance(target.get("commit_sha"), str)
    ):
        raise LifecycleBlockedError("overlay_manifest_invalid", "declared overlay contract is incomplete")
    replay_path = path.parent / str(document.get("replay_manifest"))
    if not replay_path.is_file() or hashlib.sha256(replay_path.read_bytes()).hexdigest() != document.get("replay_manifest_sha256"):
        raise LifecycleBlockedError("overlay_replay_mismatch", "declared overlay is not bound to the reviewed replay manifest")
    entries: list[tuple[str, str]] = []
    for raw_root in roots:
        relative_root = _relative(str(raw_root))
        source_root = root / relative_root
        if not source_root.is_dir() or source_root.is_symlink():
            raise LifecycleBlockedError("overlay_root_missing", f"declared overlay root is missing: {raw_root}")
        for source in sorted(source_root.rglob("*")):
            if not source.is_file() or source.is_symlink() or source.resolve() == path or "__pycache__" in source.parts or source.suffix == ".pyc":
                continue
            relative = source.relative_to(root).as_posix()
            entries.append((relative, relative))
    if len({target_path for _, target_path in entries}) != len(entries):
        raise LifecycleBlockedError("overlay_collision", "declared overlay roots overlap")
    digest = hashlib.sha256()
    for source_name, target_name in entries:
        digest.update(f"{source_name}\0{target_name}\0".encode())
        digest.update((root / source_name).read_bytes())
        digest.update(b"\0")
    source_digest = digest.hexdigest()
    if document.get("overlay_source_sha256") != source_digest:
        raise LifecycleBlockedError("overlay_source_mismatch", "declared overlay source digest does not match reviewed content")
    return OverlayManifest(target["tag"], target["commit_sha"], tuple(entries), source_digest)


def tree_digest(root: Path, *, excluded_names: tuple[str, ...] = ()) -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative_path = path.relative_to(root)
        if any(part in excluded_names for part in relative_path.parts):
            continue
        relative = relative_path.as_posix()
        if path.is_symlink():
            raise LifecycleBlockedError("composed_symlink", "composed source cannot contain symlinks")
        if path.is_file():
            digest.update(f"F\0{relative}\0".encode()); digest.update(path.read_bytes()); digest.update(b"\0")
        elif path.is_dir():
            digest.update(f"D\0{relative}\0".encode())
    return digest.hexdigest()


def _relative(value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise LifecycleBlockedError("overlay_path_invalid", "overlay path must remain relative")
    return Path(*pure.parts)


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        path.chmod(stat.S_IMODE(path.stat().st_mode) & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def _make_staging_writable(root: Path) -> None:
    root.chmod(stat.S_IMODE(root.stat().st_mode) | 0o700)
    for path in root.rglob("*"):
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode | (0o700 if path.is_dir() else 0o600))


def compose_source(official_source: Path, overlay_root: Path, destination: Path, manifest: OverlayManifest) -> ComposedSource:
    source = Path(official_source).resolve(); overlay = Path(overlay_root).resolve(); output = Path(destination).resolve()
    if source == output or source in output.parents or output in source.parents:
        raise LifecycleBlockedError("composed_source_overlap", "composed output cannot overlap immutable official source")
    if overlay == output or overlay in output.parents or output in overlay.parents:
        raise LifecycleBlockedError("composed_overlay_overlap", "composed output cannot overlap overlay source")
    if output.exists():
        raise LifecycleBlockedError("composed_destination_exists", "composed destination must be new")
    targets: set[str] = set()
    for source_name, target_name in manifest.entries:
        src = overlay / _relative(source_name); target = _relative(target_name)
        if not src.is_file() or src.is_symlink():
            raise LifecycleBlockedError("overlay_entry_missing", f"declared overlay entry is missing: {source_name}")
        if target.as_posix() in targets:
            raise LifecycleBlockedError("overlay_collision", "overlay target collision")
        targets.add(target.as_posix())
    staging = output.with_name(f".{output.name}.{os.getpid()}.staging")
    if staging.exists():
        raise LifecycleBlockedError("composed_staging_exists", "staging path already exists")
    try:
        shutil.copytree(source, staging, symlinks=False)
        # The authoritative source is immutable. Its copied directory modes
        # must be writable only while the reviewed overlay is assembled.
        _make_staging_writable(staging)
        for source_name, target_name in manifest.entries:
            target = staging / _relative(target_name)
            if target.exists():
                raise LifecycleBlockedError("overlay_upstream_collision", f"overlay cannot replace upstream path without an explicit reviewed rule: {target_name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(overlay / _relative(source_name), target)
        receipt = {
            "schema_id": "ik.hermes.composed-source.v1",
            "target_tag": manifest.target_tag,
            "target_commit_sha": manifest.target_commit_sha,
            "overlay_manifest_sha256": manifest.digest(),
        }
        (staging / "ik-composition.json").write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        digest = tree_digest(staging)
        _make_read_only(staging)
        os.replace(staging, output)
        return ComposedSource(output, digest, manifest.digest(), manifest.target_tag, manifest.target_commit_sha)
    except Exception:
        if staging.exists():
            _make_staging_writable(staging)
            shutil.rmtree(staging)
        raise
