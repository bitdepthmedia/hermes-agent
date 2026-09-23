"""Compare release input bytes with exact Git objects, never a mutable checkout."""

from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess

from .composed_source import load_declared_overlay
from .models import LifecycleBlockedError


@dataclass(frozen=True)
class SourceProvenance:
    repository: Path
    implementation_commit: str
    overlay_manifest: str | None = None


def export_committed_source(
    provenance: SourceProvenance, destination: Path, protected_roots: tuple[Path, ...]
) -> Path:
    """Export raw blobs, without checkout/archive EOL or export-attribute filters."""
    destination = Path(destination)
    resolved = destination.resolve()
    if (
        not protected_roots
        or destination.exists()
        or destination.is_symlink()
        or any(
            resolved.is_relative_to(Path(root).resolve())
            or Path(root).resolve().is_relative_to(resolved)
            for root in protected_roots
        )
    ):
        raise LifecycleBlockedError(
            "source_export_destination",
            "source export needs a new isolated destination and protected roots",
        )
    tree = _tree(provenance.repository, provenance.implementation_commit)
    result = subprocess.run(
        [
            "git",
            "--no-replace-objects",
            "-C",
            str(provenance.repository),
            "cat-file",
            "--batch",
        ],
        input="".join(oid + "\n" for _, oid in tree.values()).encode(),
        capture_output=True,
        timeout=120,
        check=True,
    )
    stream = io.BytesIO(result.stdout)
    destination.mkdir(parents=True, mode=0o700)
    for name, (mode, oid) in tree.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            raise LifecycleBlockedError(
                "source_export_path", "source export contains an unsafe path"
            )
        actual_oid, kind, size = stream.readline().decode("ascii").strip().split()
        content = stream.read(int(size))
        if (
            actual_oid != oid
            or kind != "blob"
            or len(content) != int(size)
            or stream.read(1) != b"\n"
        ):
            raise LifecycleBlockedError(
                "source_export_blob", "source export blob response is invalid"
            )
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as target:
            target.write(content)
        path.chmod(0o755 if mode == "100755" else 0o644)
    return destination


def _git(repository: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repository), *args],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        raise LifecycleBlockedError(
            "source_provenance_git", "source provenance Git objects unavailable"
        ) from exc


def _tree(repository: Path, commit: str) -> dict[str, tuple[str, str]]:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise LifecycleBlockedError(
            "source_provenance_commit", "source provenance requires exact commits"
        )
    if _git(repository, "cat-file", "-t", commit).strip() != b"commit":
        raise LifecycleBlockedError(
            "source_provenance_commit", "source provenance object is not a commit"
        )
    result = {}
    for entry in _git(repository, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise LifecycleBlockedError(
                "source_provenance_type",
                "source provenance forbids links and submodules",
            )
        result[name.decode("utf-8")] = (mode, oid)
    return result


def verify_upstream_bindings(expected, official, patches):
    allowed = {".github/workflows/contributor-check.yml"}
    if not isinstance(patches, dict) or set(patches) - allowed:
        raise LifecycleBlockedError(
            "source_ci_patch_scope", "only the reviewed attribution workflow may differ"
        )
    for name, patch in patches.items():
        before, after = official.get(name), expected.get(name)
        if (
            not isinstance(patch, dict)
            or not patch.get("authority")
            or before is None
            or after is None
            or before[0] != after[0]
            or before[1] != patch.get("upstream_blob")
            or after[1] != patch.get("implementation_blob")
            or before == after
        ):
            raise LifecycleBlockedError(
                "source_ci_patch_binding", "reviewed CI patch blob bindings differ"
            )
    if any(
        expected.get(name) != binding and name not in patches
        for name, binding in official.items()
    ):
        raise LifecycleBlockedError(
            "source_core_replacement",
            "source provenance rejects upstream core replacement or deletion",
        )


def verify_source_provenance(
    source: Path, upstream: str, tag: str, provenance: SourceProvenance | None
) -> dict[str, str]:
    if provenance is None:
        raise LifecycleBlockedError(
            "source_provenance_missing", "source provenance is required before sealing"
        )
    expected = _tree(provenance.repository, provenance.implementation_commit)
    official = _tree(provenance.repository, upstream)
    patches = {}
    if provenance.overlay_manifest:
        relative = Path(provenance.overlay_manifest)
        if relative.is_absolute() or ".." in relative.parts:
            raise LifecycleBlockedError(
                "source_overlay_missing", "source overlay path is invalid"
            )
        manifest = json.loads(
            _git(
                provenance.repository,
                "show",
                f"{provenance.implementation_commit}:{relative.as_posix()}",
            )
        )
        patches = manifest.get("non_runtime_patches", {})
    verify_upstream_bindings(expected, official, patches)
    observed = {}
    for path in Path(source).rglob("*"):
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            raise LifecycleBlockedError(
                "source_provenance_type",
                "source provenance rejects links and special files",
            )
        if path.is_file():
            content = path.read_bytes()
            oid = hashlib.sha1(
                b"blob " + str(len(content)).encode() + b"\0" + content
            ).hexdigest()
            observed[path.relative_to(source).as_posix()] = (
                "100755" if path.stat().st_mode & 0o111 else "100644",
                oid,
            )
    if observed != expected:
        raise LifecycleBlockedError(
            "source_uncommitted_drift",
            "source provenance differs from committed implementation",
        )
    additions = set(expected) - set(official)
    overlay_digest = ""
    if additions:
        relative = Path(provenance.overlay_manifest or "")
        if (
            not provenance.overlay_manifest
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise LifecycleBlockedError(
                "source_overlay_missing",
                "source provenance requires a declared overlay",
            )
        overlay = load_declared_overlay(source, source / relative)
        if overlay.target_commit_sha != upstream or overlay.target_tag != tag:
            raise LifecycleBlockedError(
                "source_overlay_target", "source provenance overlay target differs"
            )
        if additions != {destination for _, destination in overlay.entries}:
            raise LifecycleBlockedError(
                "source_undeclared_addition",
                "source provenance rejects undeclared additions",
            )
        overlay_digest = overlay.digest()
    elif provenance.overlay_manifest:
        raise LifecycleBlockedError(
            "source_overlay_unexpected",
            "source provenance overlay is not an additive customization",
        )
    return {
        "upstream_commit": upstream,
        "implementation_commit": provenance.implementation_commit,
        "overlay_manifest_sha256": overlay_digest,
    }
