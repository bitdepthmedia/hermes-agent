"""Encrypted exact-byte rollback archive with declared symlink preservation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess

from .opaque_backup import MacOSStorageAttestor, _existing_components_symlink_safe, _safe_token, _secure_mkdir


class ExactOpaqueArchiveError(RuntimeError):
    """A path- and content-free exact archive failure."""


@dataclass(frozen=True)
class ExactOpaqueArchiveRequest:
    source_root: Path
    allowed_link_root: Path
    storage_root: Path
    source_alias: str
    idempotency_key: str
    denied_roots: tuple[Path, ...] = ()


@dataclass(frozen=True)
class ExactOpaqueArchiveReceipt:
    schema_id: str
    status: str
    snapshot_id: str
    source_alias: str
    aggregate_entry_count: int
    aggregate_bytes: int
    symlink_count: int
    tree_sha256: str
    clone_tree_sha256: str
    archive_sha256: str
    archive_hmac_sha256: str
    rollback_handle: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class ExactOpaqueArchiveResult:
    receipt: ExactOpaqueArchiveReceipt
    archive_path: Path
    clone_root: Path
    key_path: Path
    receipt_path: Path


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree(root: Path, allowed_link_root: Path) -> tuple[str, int, int, int]:
    source = Path(root).absolute()
    allowed = Path(allowed_link_root).resolve(strict=True)
    digest = hashlib.sha256()
    entries = total = links = 0
    for path in sorted(source.rglob("*"), key=lambda item: os.fsencode(item.relative_to(source).as_posix())):
        relative = path.relative_to(source).as_posix().encode()
        metadata = os.lstat(path)
        if metadata.st_uid != os.getuid():
            raise ExactOpaqueArchiveError("source_owner_invalid")
        if stat.S_ISLNK(metadata.st_mode):
            try:
                resolved = path.resolve(strict=True)
            except OSError as error:
                raise ExactOpaqueArchiveError("source_symlink_target_rejected") from error
            if not _inside(resolved, allowed):
                raise ExactOpaqueArchiveError("source_symlink_target_rejected")
            target = os.fsencode(os.readlink(path))
            digest.update(b"L\0" + relative + b"\0" + target + b"\0")
            entries += 1; total += len(target); links += 1
        elif stat.S_ISDIR(metadata.st_mode):
            digest.update(b"D\0" + relative + b"\0")
            entries += 1
        elif stat.S_ISREG(metadata.st_mode):
            file_sha = _digest_file(path).encode()
            digest.update(b"F\0" + relative + b"\0" + str(metadata.st_size).encode() + b"\0" + file_sha + b"\0")
            entries += 1; total += metadata.st_size
        else:
            raise ExactOpaqueArchiveError("source_special_file_rejected")
    return digest.hexdigest(), entries, total, links


def _restrict(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_symlink():
            continue
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    os.chmod(root, 0o700)


def build_exact_opaque_archive(
    request: ExactOpaqueArchiveRequest,
    *,
    storage_clear: bool = False,
) -> ExactOpaqueArchiveResult:
    source = Path(request.source_root).absolute()
    allowed = Path(request.allowed_link_root).absolute()
    storage = Path(request.storage_root).absolute()
    alias = _safe_token(request.source_alias, "source_alias")
    key_id = _safe_token(request.idempotency_key, "idempotency_key")
    if (
        source.is_symlink()
        or not source.is_dir()
        or allowed.is_symlink()
        or not allowed.is_dir()
        or not _existing_components_symlink_safe(source)
        or not _existing_components_symlink_safe(allowed)
    ):
        raise ExactOpaqueArchiveError("source_root_invalid")
    if not storage_clear:
        attestation = MacOSStorageAttestor().attest(storage, denied_roots=request.denied_roots)
        if not attestation.clear:
            raise ExactOpaqueArchiveError("storage_policy_blocked")
    snapshot_id = hashlib.sha256(f"{alias}\0{key_id}".encode()).hexdigest()[:24]
    rollback_handle = hashlib.sha256(f"rollback\0{snapshot_id}".encode()).hexdigest()[:24]
    _secure_mkdir(storage)
    for name in ("archives", "clones", "keys", "receipts", "staging"):
        _secure_mkdir(storage / name)
    archive = storage / "archives" / f"{snapshot_id}.enc"
    clone = storage / "clones" / snapshot_id
    key_path = storage / "keys" / f"{rollback_handle}.key"
    receipt_path = storage / "receipts" / f"{snapshot_id}.json"
    if any(path.exists() or path.is_symlink() for path in (archive, clone, key_path, receipt_path)):
        raise ExactOpaqueArchiveError("snapshot_identity_collision")
    before = _tree(source, allowed)
    tar_path = storage / "staging" / f"{snapshot_id}.tar"
    key_material = secrets.token_bytes(64)
    try:
        subprocess.run(("/usr/bin/tar", "-cf", str(tar_path), "-C", str(source), "."), check=True, capture_output=True, timeout=3600)
        after = _tree(source, allowed)
        if after != before:
            raise ExactOpaqueArchiveError("concurrent_source_mutation")
        key_path.write_text(key_material.hex(), encoding="ascii")
        os.chmod(key_path, 0o400)
        subprocess.run(
            (
                "/usr/bin/openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "600000",
                "-md", "sha256", "-salt", "-in", str(tar_path), "-out", str(archive),
                "-pass", f"file:{key_path}",
            ),
            check=True,
            capture_output=True,
            timeout=3600,
        )
        os.chmod(archive, 0o400)
        clone.mkdir(mode=0o700)
        subprocess.run(("/usr/bin/tar", "-xf", str(tar_path), "-C", str(clone)), check=True, capture_output=True, timeout=3600)
        _restrict(clone)
        clone_tree = _tree(clone, allowed)
        if clone_tree != before:
            raise ExactOpaqueArchiveError("clone_integrity_failed")
        archive_sha = _digest_file(archive)
        archive_hmac = hmac.new(key_material[32:], archive.read_bytes(), hashlib.sha256).hexdigest()
        receipt = ExactOpaqueArchiveReceipt(
            "ik.hermes.exact-opaque-archive.v1", "CLEAR", snapshot_id, alias,
            before[1], before[2], before[3], before[0], clone_tree[0], archive_sha,
            archive_hmac, rollback_handle,
        )
        receipt_path.write_text(json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.chmod(receipt_path, 0o400)
        return ExactOpaqueArchiveResult(receipt, archive, clone, key_path, receipt_path)
    except ExactOpaqueArchiveError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise ExactOpaqueArchiveError("exact_archive_failed") from error
    finally:
        if tar_path.exists():
            tar_path.unlink()
