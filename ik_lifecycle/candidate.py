"""Immutable candidate construction and sealing for isolated Hermes cells."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .filesystem import (
    CellLayout,
    ensure_outside_protected,
    prepare_cell_layout,
    validate_rollback_pair,
    verify_tree_read_only,
)
from .models import CellSpec, GateSet, LifecycleBlockedError, ReleaseSelection
from .supply_chain import inspect_manifests


PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_REPLAY_MANIFEST = PACKAGE_ROOT / "manifests" / "legacy-customization-replay-v1.json"
_REPLAY_SCHEMA = "ik.hermes.customization-replay-manifest.v1"
_BUILD_SCHEMA = "ik.hermes.candidate-build-manifest.v1"
_COMMIT_SHA_LENGTH = 40
_ALLOWED_DISPOSITIONS = {"upstream-superseded", "replay-at-supported-edge", "adapt", "reject"}


@dataclass(frozen=True)
class ReplayEntry:
    order: int
    commit: str
    disposition: str
    supported_edge: str
    summary: str


@dataclass(frozen=True)
class ReplayManifest:
    path: Path
    sha256: str
    entries: tuple[ReplayEntry, ...]


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    path: Path
    manifest_path: Path
    source_path: Path
    layout: CellLayout


def _canonical_bytes(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    payload = _canonical_bytes(document) + b"\n"
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise LifecycleBlockedError("invalid_release_time", "Release evidence time lacks a timezone")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def load_replay_manifest(path: Path) -> ReplayManifest:
    """Load and fail-closed validate the declared legacy replay order."""

    manifest_path = Path(path).resolve()
    try:
        raw = manifest_path.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleBlockedError("replay_manifest_invalid", f"Cannot load replay manifest: {manifest_path}") from exc
    if not isinstance(document, dict) or document.get("schema_id") != _REPLAY_SCHEMA:
        raise LifecycleBlockedError("replay_manifest_invalid", "Replay manifest schema id is invalid")
    raw_entries = document.get("entries")
    if not isinstance(raw_entries, list) or len(raw_entries) != 12:
        raise LifecycleBlockedError("replay_manifest_incomplete", "Replay manifest must declare exactly 12 legacy commits")
    entries: list[ReplayEntry] = []
    for raw_entry in raw_entries:
        if not isinstance(raw_entry, dict):
            raise LifecycleBlockedError("replay_manifest_invalid", "Replay entry must be an object")
        try:
            entry = ReplayEntry(
                order=raw_entry["order"],
                commit=raw_entry["commit"],
                disposition=raw_entry["disposition"],
                supported_edge=raw_entry["supported_edge"],
                summary=raw_entry["summary"],
            )
        except KeyError as exc:
            raise LifecycleBlockedError("replay_manifest_invalid", "Replay entry is missing a required field") from exc
        if not isinstance(entry.order, int) or not isinstance(entry.commit, str) or len(entry.commit) != _COMMIT_SHA_LENGTH:
            raise LifecycleBlockedError("replay_manifest_invalid", "Replay entry order or commit is invalid")
        try:
            int(entry.commit, 16)
        except ValueError as exc:
            raise LifecycleBlockedError("replay_manifest_invalid", "Replay commit must be a full hexadecimal SHA") from exc
        if entry.disposition not in _ALLOWED_DISPOSITIONS:
            raise LifecycleBlockedError("replay_manifest_invalid", "Replay disposition is invalid")
        if not isinstance(entry.supported_edge, str) or not entry.supported_edge or not isinstance(entry.summary, str) or not entry.summary:
            raise LifecycleBlockedError("replay_manifest_invalid", "Replay supported edge and summary are required")
        entries.append(entry)
    if [entry.order for entry in entries] != list(range(1, 13)):
        raise LifecycleBlockedError("replay_manifest_order", "Replay entries must be ordered from 1 through 12")
    if len({entry.commit for entry in entries}) != len(entries):
        raise LifecycleBlockedError("replay_manifest_duplicate", "Replay commits must be unique")
    return ReplayManifest(manifest_path, hashlib.sha256(raw).hexdigest(), tuple(entries))


def _git(source: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise LifecycleBlockedError("source_git_invalid", result.stderr.strip() or "Source Git inspection failed")
    return result.stdout.strip()


def _validate_source_links(source: Path) -> None:
    source_root = source.resolve()
    for path in source.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            target = path.resolve(strict=True)
        except OSError as exc:
            raise LifecycleBlockedError("source_symlink_invalid", f"Broken source symlink: {path}") from exc
        if target != source_root and source_root not in target.parents:
            raise LifecycleBlockedError("source_symlink_escape", f"Source symlink escapes snapshot root: {path}")


def _tracked_case_collision_groups(source: Path) -> tuple[tuple[str, ...], ...]:
    groups: dict[str, list[str]] = {}
    for tracked_path in _git(source, "ls-tree", "-r", "--name-only", "HEAD").splitlines():
        key = unicodedata.normalize("NFC", tracked_path).casefold()
        groups.setdefault(key, []).append(tracked_path)
    return tuple(tuple(sorted(paths)) for paths in groups.values() if len(paths) > 1)


def _unrepresentable_case_collisions(source: Path) -> tuple[str, ...]:
    unsupported: list[str] = []
    for paths in _tracked_case_collision_groups(source):
        try:
            identities = [(source / path).lstat() for path in paths]
        except OSError:
            unsupported.extend(paths)
            continue
        inode_keys = {(identity.st_dev, identity.st_ino) for identity in identities}
        if len(inode_keys) != len(paths):
            unsupported.extend(paths)
    return tuple(sorted(unsupported))


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(path).encode("utf-8") + b"\0")
        elif path.is_file():
            digest.update(b"F\0" + relative + b"\0")
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            digest.update(b"\0")
        elif path.is_dir():
            digest.update(b"D\0" + relative + b"\0")
    return digest.hexdigest()


def _selection_document(selection: ReleaseSelection) -> dict[str, Any]:
    return {
        "discovered_at": _utc(selection.discovered_at),
        "latest": {
            "tag": selection.latest.tag,
            "commit_sha": selection.latest.commit_sha,
            "published_at": _utc(selection.latest.published_at),
            "html_url": selection.latest.html_url,
        },
        "target": {
            "tag": selection.target.tag,
            "commit_sha": selection.target.commit_sha,
            "published_at": _utc(selection.target.published_at),
            "html_url": selection.target.html_url,
        },
        "policy": "immediately_previous_published_stable_release",
    }


def _candidate_id(selection: ReleaseSelection, cell: CellSpec, replay: ReplayManifest) -> str:
    identity = {
        "cell_id": cell.cell_id,
        "target_commit_sha": selection.target.commit_sha,
        "target_tag": selection.target.tag,
        "replay_sha256": replay.sha256,
    }
    return hashlib.sha256(_canonical_bytes(identity)).hexdigest()[:20]


def _base_manifest(
    selection: ReleaseSelection,
    cell: CellSpec,
    replay: ReplayManifest,
    candidate_id: str,
) -> dict[str, Any]:
    blockers = []
    if cell.legacy_health_automation_status != "PAUSED":
        blockers.append("legacy_health_automation_pause")
    return {
        "schema_id": _BUILD_SCHEMA,
        "candidate_id": candidate_id,
        "status": "BUILDING",
        "cell": {"id": cell.cell_id, "trust_zone": cell.trust_zone},
        "release_selection": _selection_document(selection),
        "source": {"commit_sha": selection.target.commit_sha, "tree_sha256": None},
        "replay": {
            "manifest": replay.path.name,
            "sha256": replay.sha256,
            "entry_count": len(replay.entries),
        },
        "supply_chain": {"status": "pending", "code": "pending", "findings": [], "hook_changes": []},
        "dependency_phase": {
            "status": "approval_required",
            "execution_performed": False,
            "planned_commands": [],
        },
        "promotion_blockers": blockers,
        "external_approval_gates": {
            "computer_history_path_adaptation": cell.computer_history_path_status,
        },
    }


def _failed(
    manifest_path: Path,
    manifest: dict[str, Any],
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    manifest["status"] = "FAILED"
    manifest["failure"] = {"code": code, "message": message}
    manifest["failure"].update(details or {})
    _write_json(manifest_path, manifest)


def _candidate_from_path(candidate_id: str, path: Path, layout: CellLayout) -> Candidate:
    return Candidate(candidate_id, path, path / "build-manifest.json", path / "source", layout)


def _verify_existing(candidate: Candidate, selection: ReleaseSelection, cell: CellSpec, replay: ReplayManifest) -> Candidate:
    if not candidate.manifest_path.is_file():
        raise LifecycleBlockedError("existing_non_candidate_path", f"Candidate path exists without a manifest: {candidate.path}")
    try:
        document = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleBlockedError("candidate_manifest_invalid", f"Cannot read candidate manifest: {candidate.path}") from exc
    valid = (
        document.get("schema_id") == _BUILD_SCHEMA
        and document.get("candidate_id") == candidate.candidate_id
        and document.get("cell", {}).get("id") == cell.cell_id
        and document.get("release_selection", {}).get("target", {}).get("commit_sha") == selection.target.commit_sha
        and document.get("replay", {}).get("sha256") == replay.sha256
    )
    if not valid:
        raise LifecycleBlockedError("candidate_identity_mismatch", f"Existing candidate identity does not match: {candidate.path}")
    if document.get("status") == "FAILED":
        raise LifecycleBlockedError("candidate_failed", f"Existing candidate is retained as failed: {candidate.path}")
    if document.get("status") not in {"STATIC_PREPARED", "SEALED"} or not candidate.source_path.is_dir():
        raise LifecycleBlockedError("candidate_incomplete", f"Existing candidate is incomplete: {candidate.path}")
    expected_tree = document.get("source", {}).get("tree_sha256")
    if not isinstance(expected_tree, str) or _tree_digest(candidate.source_path) != expected_tree:
        raise LifecycleBlockedError("candidate_tree_mismatch", f"Existing candidate payload was modified: {candidate.path}")
    verify_tree_read_only(candidate.source_path)
    return candidate


def build_candidate(
    selection: ReleaseSelection,
    cell: CellSpec,
    platform_root: Path,
    *,
    source: Path,
    replay_manifest: Path = DEFAULT_REPLAY_MANIFEST,
) -> Candidate:
    """Snapshot a pre-screened exact-target checkout without installing anything."""

    source_path = Path(source).resolve()
    platform_path = Path(os.path.abspath(os.fspath(platform_root)))
    if source_path == platform_path or source_path in platform_path.parents or platform_path in source_path.parents:
        raise LifecycleBlockedError(
            "source_candidate_overlap",
            "Candidate platform and source checkout must be independent paths",
        )
    ensure_outside_protected(source_path, cell.protected_paths)
    if not source_path.is_dir() or not (source_path / ".git").exists():
        raise LifecycleBlockedError("source_git_invalid", f"Source is not a Git checkout: {source_path}")
    replay = load_replay_manifest(replay_manifest)
    layout = prepare_cell_layout(platform_path, cell.cell_id, protected_paths=cell.protected_paths)
    candidate_id = _candidate_id(selection, cell, replay)
    candidate_path = layout.candidates / candidate_id
    candidate = _candidate_from_path(candidate_id, candidate_path, layout)
    if candidate.path.is_symlink():
        raise LifecycleBlockedError("symlink_escape", f"Candidate identity path cannot be a symlink: {candidate.path}")
    if candidate.path.exists():
        return _verify_existing(candidate, selection, cell, replay)
    candidate.path.mkdir(mode=0o700)
    manifest = _base_manifest(selection, cell, replay, candidate_id)
    _write_json(candidate.manifest_path, manifest)

    try:
        actual_head = _git(source_path, "rev-parse", "HEAD").lower()
        if actual_head != selection.target.commit_sha.lower():
            raise LifecycleBlockedError(
                "source_head_mismatch",
                f"Source HEAD {actual_head} does not match canonical target {selection.target.commit_sha}",
            )
        case_collisions = _unrepresentable_case_collisions(source_path)
        if case_collisions:
            raise LifecycleBlockedError(
                "source_case_collision",
                "Source contains tracked paths that cannot coexist on a case-insensitive filesystem",
                details={"paths": list(case_collisions)},
            )
        if _git(source_path, "status", "--porcelain"):
            raise LifecycleBlockedError("source_dirty", "Candidate source checkout is not clean")
        _validate_source_links(source_path)
        shutil.copytree(
            source_path,
            candidate.source_path,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".pytest_cache"),
        )
        manifest["source"]["tree_sha256"] = _tree_digest(candidate.source_path)
        supply_chain = inspect_manifests(candidate.source_path)
        manifest["supply_chain"] = {
            "status": supply_chain.status,
            "code": supply_chain.code,
            "findings": [asdict(item) for item in supply_chain.findings],
            "hook_changes": [asdict(item) for item in supply_chain.hook_changes],
            "artifact_sha256": dict(supply_chain.artifact_sha256),
        }
        manifest["dependency_phase"]["planned_commands"] = [
            {"workdir": command.workdir, "argv": list(command.argv)} for command in supply_chain.planned_commands
        ]
        if supply_chain.status != "CLEAR":
            raise LifecycleBlockedError("forbidden_dependency", "Candidate contains forbidden dependency evidence")
        _make_tree_read_only(candidate.source_path)
        verify_tree_read_only(candidate.source_path)
        manifest["status"] = "STATIC_PREPARED"
        _write_json(candidate.manifest_path, manifest)
        _write_json(
            layout.state / "target.json",
            {
                "schema_id": "ik.hermes.cell-target.v1",
                "cell_id": cell.cell_id,
                "target": selection.target.tag,
                "target_commit_sha": selection.target.commit_sha,
                "candidate_id": candidate_id,
                "candidate_status": "STATIC_PREPARED",
            },
        )
        return candidate
    except LifecycleBlockedError as exc:
        _failed(candidate.manifest_path, manifest, exc.code, str(exc), details=exc.details)
        raise
    except Exception as exc:
        _failed(candidate.manifest_path, manifest, "candidate_build_failed", str(exc))
        raise LifecycleBlockedError("candidate_build_failed", f"Candidate snapshot failed: {exc}") from exc


def _make_tree_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            raise LifecycleBlockedError("sealed_release_symlink", f"Cannot seal a symlinked release tree: {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        path.chmod(mode & ~0o222)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def _load_verified_receipt(path: Path, *, kind: str, status: str, missing_code: str) -> tuple[dict[str, Any], str]:
    if path is None or not Path(path).is_file() or Path(path).is_symlink():
        raise LifecycleBlockedError(missing_code, f"Required {kind} receipt is missing")
    try:
        raw = Path(path).read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleBlockedError("approval_receipt_invalid", f"Cannot read {kind} receipt") from exc
    body = document.get("receipt") if isinstance(document, dict) else None
    digest = document.get("sha256") if isinstance(document, dict) else None
    if not isinstance(body, dict) or digest != hashlib.sha256(_canonical_bytes(body)).hexdigest():
        raise LifecycleBlockedError("approval_receipt_invalid", f"{kind} receipt digest is invalid")
    if body.get("kind") != kind or body.get("status") != status or not isinstance(body.get("data"), dict):
        raise LifecycleBlockedError("approval_receipt_invalid", f"{kind} receipt scope is invalid")
    return body["data"], hashlib.sha256(raw).hexdigest()


def _validate_dependency_receipts(candidate: Candidate, manifest: dict[str, Any], gates: GateSet) -> None:
    approval, approval_file_sha = _load_verified_receipt(
        gates.dependency_approval_receipt,
        kind="dependency_execution_approval",
        status="APPROVED",
        missing_code="dependency_approval_required",
    )
    planned_sha = hashlib.sha256(_canonical_bytes(manifest["dependency_phase"]["planned_commands"])).hexdigest()
    expected = {
        "candidate_id": candidate.candidate_id,
        "candidate_tree_sha256": manifest["source"]["tree_sha256"],
        "planned_commands_sha256": planned_sha,
    }
    if any(approval.get(key) != value for key, value in expected.items()):
        raise LifecycleBlockedError("approval_receipt_mismatch", "Dependency approval does not match this candidate")
    install, _ = _load_verified_receipt(
        gates.dependency_install_receipt,
        kind="dependency_install_result",
        status="CLEAR",
        missing_code="dependency_install_receipt_missing",
    )
    install_expected = {
        "candidate_id": candidate.candidate_id,
        "candidate_tree_sha256": manifest["source"]["tree_sha256"],
        "approval_file_sha256": approval_file_sha,
    }
    if any(install.get(key) != value for key, value in install_expected.items()):
        raise LifecycleBlockedError("dependency_install_receipt_mismatch", "Dependency install evidence is not bound to this approval")


def seal_candidate(candidate: Candidate, gates: GateSet) -> Path:
    """Seal a proven candidate without changing any live release pointer."""

    required = {
        "static_scan_clear": gates.static_scan_clear,
        "source_identity_clear": gates.source_identity_clear,
        "tests_clear": gates.tests_clear,
        "hooks_reviewed": gates.hooks_reviewed,
        "dependency_install_clear": gates.dependency_install_clear,
    }
    missing = [name for name, clear in required.items() if not clear]
    if missing:
        raise LifecycleBlockedError("candidate_gate_incomplete", f"Candidate gates are incomplete: {', '.join(missing)}")
    if gates.rollback_release_pointer is None or gates.rollback_profile_pointer is None:
        raise LifecycleBlockedError("rollback_prerequisite_missing", "A rollback release/profile pointer pair is required")
    rollback = validate_rollback_pair(
        candidate.layout,
        gates.rollback_release_pointer,
        gates.rollback_profile_pointer,
    )
    manifest = json.loads(candidate.manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "STATIC_PREPARED":
        raise LifecycleBlockedError("candidate_not_prepared", "Only a statically prepared candidate can be sealed")
    _validate_dependency_receipts(candidate, manifest, gates)
    release_name = f"{manifest['release_selection']['target']['tag']}-{manifest['source']['commit_sha'][:12]}"
    release_path = candidate.layout.releases / release_name
    if release_path.exists():
        verify_tree_read_only(release_path)
        return release_path
    temporary = candidate.layout.releases / f".{release_name}.{os.getpid()}.staging"
    try:
        shutil.copytree(candidate.source_path, temporary, symlinks=True)
        temporary.chmod(stat.S_IMODE(temporary.stat().st_mode) | 0o200)
        _write_json(
            temporary / "release-manifest.json",
            {
                "schema_id": "ik.hermes.immutable-release.v1",
                "candidate_id": candidate.candidate_id,
                "source_tree_sha256": manifest["source"]["tree_sha256"],
                "rollback_release": str(rollback.release),
                "rollback_profile": str(rollback.profile),
                "promotion_performed": False,
            },
        )
        _make_tree_read_only(temporary)
        os.replace(temporary, release_path)
        verify_tree_read_only(release_path)
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    manifest["status"] = "SEALED"
    manifest["sealed_release"] = {"path": str(release_path), "promotion_performed": False}
    _write_json(candidate.manifest_path, manifest)
    return release_path
