"""Fail-closed, content-opaque local profile backup and clone tooling.

This module deliberately does not inspect, parse, query, transform, or report
profile content.  Paths are used internally to perform byte copies and SQLite's
online backup operation; only aggregate, opaque evidence crosses the receipt
boundary.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import plistlib
import secrets
import shutil
import sqlite3
import stat
import subprocess
from typing import Callable, Iterable
from urllib.parse import quote


_RECEIPT_SCHEMA = "ik.opaque-continuity-receipt.v1"
_DATABASE_SUFFIXES = frozenset({".sqlite", ".sqlite3"})
_ROOT_DATABASE_NAMES = frozenset(
    {
        "hermes_state.db",
        "kanban.db",
        "memory_store.db",
        "response_store.db",
        "retaindb_queue.db",
        "state.db",
    }
)
_SYNC_COMPONENTS = frozenset(
    {
        "cloudstorage",
        "mobile documents",
        "icloud drive",
        "dropbox",
        "google drive",
        "onedrive",
        "box",
    }
)
_OPENSSL = Path("/usr/bin/openssl")
_TAR = Path("/usr/bin/tar")


class OpaqueBackupError(RuntimeError):
    """An intentionally path- and content-free backup failure."""


@dataclass(frozen=True)
class StorageAttestation:
    encrypted_at_rest: bool
    local: bool
    non_synced: bool
    current_user_owned: bool
    symlink_safe: bool
    outside_git: bool
    outside_shared_memory: bool
    verifier_digest: str

    @property
    def clear(self) -> bool:
        return all(
            (
                self.encrypted_at_rest,
                self.local,
                self.non_synced,
                self.current_user_owned,
                self.symlink_safe,
                self.outside_git,
                self.outside_shared_memory,
            )
        )


@dataclass(frozen=True)
class OpaqueBackupRequest:
    source_root: Path
    storage_root: Path
    source_alias: str
    storage_alias: str
    idempotency_key: str
    denied_roots: tuple[Path, ...] = ()


@dataclass(frozen=True)
class OpaqueBackupReceipt:
    schema_version: str
    snapshot_id: str
    source_alias: str
    storage_alias: str
    source_path_sha256: str
    storage_attestation_sha256: str
    created_at: str
    aggregate_file_count: int
    aggregate_bytes: int
    snapshot_tree_sha256: str
    clone_tree_sha256: str
    archive_sha256: str
    archive_hmac_sha256: str
    receipt_hmac_sha256: str
    encryption: str
    permission_state: str
    rollback_handle: str
    status: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class OpaqueBackupResult:
    """In-process locations; never serialize this object as a receipt."""

    receipt: OpaqueBackupReceipt
    backup_root: Path
    archive_path: Path
    clone_root: Path
    key_path: Path
    receipt_path: Path


def derive_ernie_profile_root(git_common_dir: Path) -> Path:
    """Derive the canonical profile root from repository topology metadata."""

    common = Path(git_common_dir).expanduser().absolute()
    canonical_checkout = common.parent
    stack_root = canonical_checkout.parent.parent
    return stack_root / "config" / "ik-agents" / "hermes-ernie"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hmac_file(path: Path, key: bytes) -> str:
    digest = hmac.new(key, digestmod=hashlib.sha256)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_token(value: str, label: str) -> str:
    if not value or len(value) > 128 or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in value):
        raise OpaqueBackupError(f"invalid_{label}")
    return value


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _nearest_existing(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        if candidate.parent == candidate:
            raise OpaqueBackupError("storage_ancestor_missing")
        candidate = candidate.parent
    return candidate


def _existing_components_symlink_safe(path: Path) -> bool:
    existing = _nearest_existing(path)
    current = Path(existing.anchor)
    for component in existing.parts[1:]:
        current = current / component
        if stat.S_ISLNK(os.lstat(current).st_mode):
            return False
    return True


def _inside_git(path: Path) -> bool:
    candidate = _nearest_existing(path)
    for parent in (candidate, *candidate.parents):
        if (parent / ".git").exists():
            return True
    return False


def _default_volume_probe(path: Path) -> tuple[bool, bool, str]:
    try:
        df = subprocess.run(
            ["/bin/df", "-P", os.fspath(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        device = df.stdout.splitlines()[-1].split()[0]
        info = subprocess.run(
            ["/usr/sbin/diskutil", "info", "-plist", device],
            check=True,
            capture_output=True,
            timeout=15,
        )
        metadata = plistlib.loads(info.stdout)
    except (IndexError, OSError, plistlib.InvalidFileException, subprocess.SubprocessError, ValueError) as error:
        raise OpaqueBackupError("storage_attestation_unavailable") from error
    local = bool(metadata.get("Internal", False)) and device.startswith("/dev/")
    encrypted = bool(metadata.get("FileVault", False) or metadata.get("Encrypted", False))
    filesystem = str(metadata.get("FilesystemType", "unknown")).lower()
    return local, encrypted, filesystem


class MacOSStorageAttestor:
    def __init__(self, volume_probe: Callable[[Path], tuple[bool, bool, str]] = _default_volume_probe) -> None:
        self._volume_probe = volume_probe

    def attest(self, storage_root: Path, *, denied_roots: Iterable[Path]) -> StorageAttestation:
        root = Path(storage_root).expanduser().absolute()
        ancestor = _nearest_existing(root)
        local, encrypted, filesystem = self._volume_probe(ancestor)
        components = {component.lower() for component in root.parts}
        denied = tuple(Path(item).expanduser().absolute() for item in denied_roots)
        outside_shared = not any(_path_is_within(root, item) or _path_is_within(item, root) for item in denied)
        evidence = {
            "encrypted": encrypted,
            "filesystem": filesystem,
            "local": local,
            "non_synced": not bool(components & _SYNC_COMPONENTS),
            "owner": os.lstat(ancestor).st_uid == os.getuid(),
            "symlink_safe": _existing_components_symlink_safe(root),
            "outside_git": not _inside_git(root),
            "outside_shared": outside_shared,
        }
        verifier_digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return StorageAttestation(
            encrypted_at_rest=encrypted,
            local=local,
            non_synced=evidence["non_synced"],
            current_user_owned=evidence["owner"],
            symlink_safe=evidence["symlink_safe"],
            outside_git=evidence["outside_git"],
            outside_shared_memory=evidence["outside_shared"],
            verifier_digest=verifier_digest,
        )


def _secure_mkdir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or os.lstat(path).st_uid != os.getuid():
        raise OpaqueBackupError("storage_ownership_or_symlink_invalid")
    os.chmod(path, 0o700)
    if stat.S_IMODE(os.lstat(path).st_mode) != 0o700:
        raise OpaqueBackupError("storage_directory_permissions_invalid")


def _source_entries(source_root: Path) -> tuple[tuple[Path, str, bool], ...]:
    entries: list[tuple[Path, str, bool]] = []
    stack = [source_root]
    while stack:
        directory = stack.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda item: os.fsencode(item.name))
        except OSError as error:
            raise OpaqueBackupError("source_enumeration_failed") from error
        for child in children:
            relative = os.path.relpath(child.path, source_root)
            try:
                child_stat = child.stat(follow_symlinks=False)
            except OSError as error:
                raise OpaqueBackupError("source_metadata_failed") from error
            if stat.S_ISLNK(child_stat.st_mode):
                raise OpaqueBackupError("source_symlink_rejected")
            if stat.S_ISDIR(child_stat.st_mode):
                entries.append((Path(child.path), relative, True))
                stack.append(Path(child.path))
            elif stat.S_ISREG(child_stat.st_mode):
                entries.append((Path(child.path), relative, False))
            else:
                raise OpaqueBackupError("source_special_file_rejected")
    return tuple(sorted(entries, key=lambda item: os.fsencode(item[1])))


def _stat_fingerprint(path: Path) -> tuple[int, int, int, int, int]:
    metadata = os.lstat(path)
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _is_declared_sqlite(relative: str) -> bool:
    candidate = Path(relative)
    parts = tuple(part.lower() for part in candidate.parts)
    if candidate.suffix.lower() in _DATABASE_SUFFIXES:
        return True
    if len(parts) == 1 and parts[0] in _ROOT_DATABASE_NAMES:
        return True
    return bool(
        parts[-2:] == ("daily-goal", "daily-goal.db")
        or parts[-3:] == ("matrix", "store", "crypto.db")
        or (parts[-1:] == ("kanban.db",) and "boards" in parts[:-1])
    )


def _copy_regular_opaquely(source: Path, destination: Path, after_copy: Callable[[Path], None] | None) -> None:
    before = _stat_fingerprint(source)
    with source.open("rb", buffering=0) as source_stream, destination.open("xb", buffering=0) as destination_stream:
        os.chmod(destination, 0o600)
        shutil.copyfileobj(source_stream, destination_stream, length=1024 * 1024)
        destination_stream.flush()
        os.fsync(destination_stream.fileno())
    if after_copy is not None:
        after_copy(source)
    if _stat_fingerprint(source) != before:
        raise OpaqueBackupError("concurrent_source_mutation")


def _backup_sqlite_opaquely(source: Path, destination: Path) -> None:
    """Use only SQLite's online backup API; do not issue SQL or PRAGMA calls."""

    if not source.exists() or not source.is_file() or source.is_symlink():
        raise OpaqueBackupError("sqlite_source_open_failed")
    sidecars = tuple(source.with_name(source.name + suffix) for suffix in ("-wal", "-shm", "-journal"))
    use_immutable_read = not any(path.exists() for path in sidecars)
    snapshot_before = (_stat_fingerprint(source), tuple(_stat_fingerprint(path) if path.exists() else None for path in sidecars))
    query = "mode=ro&immutable=1" if use_immutable_read else "mode=ro"
    source_uri = f"file:{quote(source.as_posix(), safe='/')}?{query}"
    source_database: sqlite3.Connection | None = None
    destination_database: sqlite3.Connection | None = None
    try:
        source_database = sqlite3.connect(source_uri, uri=True, timeout=30)
    except sqlite3.Error as error:
        raise OpaqueBackupError("sqlite_source_open_failed") from error
    try:
        destination_database = sqlite3.connect(destination, timeout=30)
    except sqlite3.Error as error:
        if source_database is not None:
            source_database.close()
        raise OpaqueBackupError("sqlite_destination_open_failed") from error
    try:
        source_database.backup(destination_database, pages=256, sleep=0.050)
    except sqlite3.Error as error:
        error_name = str(getattr(error, "sqlite_errorname", ""))
        normalized_message = str(error).lower()
        code = {
            "SQLITE_BUSY": "sqlite_online_backup_busy",
            "SQLITE_LOCKED": "sqlite_online_backup_busy",
            "SQLITE_NOTADB": "sqlite_online_backup_not_database",
            "SQLITE_CANTOPEN": "sqlite_online_backup_unavailable",
        }.get(error_name)
        if code is None:
            if "not a database" in normalized_message or "file is encrypted" in normalized_message:
                code = "sqlite_online_backup_not_database"
            elif "locked" in normalized_message or "busy" in normalized_message:
                code = "sqlite_online_backup_busy"
            elif "unable to open" in normalized_message or "cannot open" in normalized_message:
                code = "sqlite_online_backup_unavailable"
            else:
                code = "sqlite_online_backup_failed"
        raise OpaqueBackupError(code) from error
    finally:
        if destination_database is not None:
            destination_database.close()
        if source_database is not None:
            source_database.close()
    if use_immutable_read:
        snapshot_after = (_stat_fingerprint(source), tuple(_stat_fingerprint(path) if path.exists() else None for path in sidecars))
        if snapshot_after != snapshot_before:
            raise OpaqueBackupError("concurrent_sqlite_source_mutation")
    os.chmod(destination, 0o600)


def _tree_digest(root: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for path, relative, is_directory in _source_entries(root):
        relative_bytes = os.fsencode(relative)
        if is_directory:
            digest.update(b"D\0" + relative_bytes + b"\0")
            continue
        file_digest = _sha256_file(path)
        size = path.stat().st_size
        digest.update(b"F\0" + relative_bytes + b"\0" + str(size).encode() + b"\0" + file_digest.encode() + b"\0")
        file_count += 1
        total_bytes += size
    return digest.hexdigest(), file_count, total_bytes


def _receipt_payload(receipt: OpaqueBackupReceipt) -> bytes:
    values = receipt.to_dict()
    values["receipt_hmac_sha256"] = ""
    return json.dumps(values, sort_keys=True, separators=(",", ":")).encode()


def _write_private_json(path: Path, payload: dict[str, object]) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(path, 0o600)


def _clone_permissions_clear(root: Path) -> bool:
    if stat.S_IMODE(os.lstat(root).st_mode) != 0o700:
        return False
    for path, _, is_directory in _source_entries(root):
        expected = 0o700 if is_directory else 0o600
        if stat.S_IMODE(os.lstat(path).st_mode) != expected:
            return False
    return True


class OpaqueBackupEngine:
    def __init__(
        self,
        *,
        attestor: MacOSStorageAttestor | None = None,
        after_regular_copy: Callable[[Path], None] | None = None,
    ) -> None:
        self._attestor = attestor or MacOSStorageAttestor()
        self._after_regular_copy = after_regular_copy

    def execute(self, request: OpaqueBackupRequest) -> OpaqueBackupResult:
        source_alias = _safe_token(request.source_alias, "source_alias")
        storage_alias = _safe_token(request.storage_alias, "storage_alias")
        idempotency_key = _safe_token(request.idempotency_key, "idempotency_key")
        source = Path(request.source_root).expanduser().absolute()
        storage = Path(request.storage_root).expanduser().absolute()
        if not source.exists() or not source.is_dir() or source.is_symlink() or not _existing_components_symlink_safe(source):
            raise OpaqueBackupError("source_root_invalid")
        if os.lstat(source).st_uid != os.getuid():
            raise OpaqueBackupError("source_owner_invalid")
        attestation = self._attestor.attest(storage, denied_roots=request.denied_roots)
        if not attestation.clear:
            raise OpaqueBackupError("storage_policy_blocked")

        snapshot_id = hashlib.sha256(f"{source_alias}\0{storage_alias}\0{idempotency_key}".encode()).hexdigest()[:24]
        rollback_handle = hashlib.sha256(f"rollback\0{snapshot_id}".encode()).hexdigest()[:24]
        source_path_sha256 = hashlib.sha256(os.fsencode(os.fspath(source))).hexdigest()
        paths = self._paths(storage, snapshot_id, rollback_handle)
        if paths["receipt"].exists():
            return self._verify_existing(paths, source_path_sha256, attestation.verifier_digest)

        self._prepare_storage(storage)
        if not self._attestor.attest(storage, denied_roots=request.denied_roots).clear:
            raise OpaqueBackupError("storage_policy_changed")
        failure_root = storage / "failures" / snapshot_id
        try:
            return self._build(
                source=source,
                source_alias=source_alias,
                storage_alias=storage_alias,
                snapshot_id=snapshot_id,
                rollback_handle=rollback_handle,
                source_path_sha256=source_path_sha256,
                storage_attestation_sha256=attestation.verifier_digest,
                paths=paths,
            )
        except Exception as error:
            self._retain_failure(paths, failure_root, error)
            if isinstance(error, OpaqueBackupError):
                raise
            raise OpaqueBackupError("opaque_backup_failed") from error

    @staticmethod
    def _paths(storage: Path, snapshot_id: str, rollback_handle: str) -> dict[str, Path]:
        return {
            "storage": storage,
            "staging": storage / "staging" / snapshot_id,
            "backup": storage / "backups" / snapshot_id,
            "archive": storage / "backups" / snapshot_id / "snapshot.enc",
            "archive_hmac": storage / "backups" / snapshot_id / "snapshot.hmac",
            "clone": storage / "clones" / snapshot_id,
            "key": storage / "keys" / f"{rollback_handle}.key",
            "receipt": storage / "receipts" / f"{snapshot_id}.json",
        }

    @staticmethod
    def _prepare_storage(storage: Path) -> None:
        _secure_mkdir(storage)
        for name in ("staging", "backups", "clones", "keys", "receipts", "failures"):
            _secure_mkdir(storage / name)

    def _build(
        self,
        *,
        source: Path,
        source_alias: str,
        storage_alias: str,
        snapshot_id: str,
        rollback_handle: str,
        source_path_sha256: str,
        storage_attestation_sha256: str,
        paths: dict[str, Path],
    ) -> OpaqueBackupResult:
        entries_before = _source_entries(source)
        _secure_mkdir(paths["staging"])
        database_relatives = {
            relative
            for _, relative, is_directory in entries_before
            if not is_directory and _is_declared_sqlite(relative)
        }
        sidecar_relatives = {f"{relative}-wal" for relative in database_relatives} | {
            f"{relative}-shm" for relative in database_relatives
        }

        for source_path, relative, is_directory in entries_before:
            if relative in sidecar_relatives:
                continue
            destination = paths["staging"] / relative
            if is_directory:
                _secure_mkdir(destination)
            else:
                _secure_mkdir(destination.parent)
                if relative in database_relatives:
                    _backup_sqlite_opaquely(source_path, destination)
                else:
                    _copy_regular_opaquely(source_path, destination, self._after_regular_copy)

        if tuple((relative, is_directory) for _, relative, is_directory in _source_entries(source)) != tuple(
            (relative, is_directory) for _, relative, is_directory in entries_before
        ):
            raise OpaqueBackupError("concurrent_source_tree_mutation")

        snapshot_digest, file_count, total_bytes = _tree_digest(paths["staging"])
        shutil.copytree(paths["staging"], paths["clone"], copy_function=shutil.copyfile)
        self._restrict_clone(paths["clone"])
        clone_digest, clone_count, clone_bytes = _tree_digest(paths["clone"])
        if (snapshot_digest, file_count, total_bytes) != (clone_digest, clone_count, clone_bytes):
            raise OpaqueBackupError("clone_integrity_failed")

        _secure_mkdir(paths["backup"])
        key_material = secrets.token_bytes(64)
        with paths["key"].open("xb") as key_stream:
            key_stream.write(key_material.hex().encode("ascii"))
            key_stream.flush()
            os.fsync(key_stream.fileno())
        os.chmod(paths["key"], 0o400)
        self._encrypt_snapshot(paths["staging"], paths["archive"], paths["key"], paths["storage"])
        archive_sha = _sha256_file(paths["archive"])
        archive_hmac = _hmac_file(paths["archive"], key_material[32:])
        with paths["archive_hmac"].open("x", encoding="ascii") as hmac_stream:
            hmac_stream.write(archive_hmac + "\n")
        os.chmod(paths["archive_hmac"], 0o400)

        receipt = OpaqueBackupReceipt(
            schema_version=_RECEIPT_SCHEMA,
            snapshot_id=snapshot_id,
            source_alias=source_alias,
            storage_alias=storage_alias,
            source_path_sha256=source_path_sha256,
            storage_attestation_sha256=storage_attestation_sha256,
            created_at=datetime.now(timezone.utc).isoformat(),
            aggregate_file_count=file_count,
            aggregate_bytes=total_bytes,
            snapshot_tree_sha256=snapshot_digest,
            clone_tree_sha256=clone_digest,
            archive_sha256=archive_sha,
            archive_hmac_sha256=archive_hmac,
            receipt_hmac_sha256="",
            encryption="AES-256-CBC-PBKDF2-SHA256-HMAC-SHA256+FileVault",
            permission_state="dirs-0700-files-0600-backup-sealed",
            rollback_handle=rollback_handle,
            status="CLEAR",
        )
        receipt = replace(receipt, receipt_hmac_sha256=hmac.new(key_material[:32], _receipt_payload(receipt), hashlib.sha256).hexdigest())
        _write_private_json(paths["receipt"], receipt.to_dict())
        shutil.rmtree(paths["staging"])
        self._seal_backup(paths["backup"])
        os.chmod(paths["receipt"], 0o400)
        return OpaqueBackupResult(receipt, paths["backup"], paths["archive"], paths["clone"], paths["key"], paths["receipt"])

    @staticmethod
    def _encrypt_snapshot(staging: Path, archive: Path, key: Path, storage: Path) -> None:
        temporary_tar = storage / "staging" / f"{archive.parent.name}.tar"
        try:
            with temporary_tar.open("xb"):
                pass
            os.chmod(temporary_tar, 0o600)
            subprocess.run(
                [os.fspath(_TAR), "-cf", os.fspath(temporary_tar), "-C", os.fspath(staging), "."],
                check=True,
                capture_output=True,
                timeout=3600,
            )
            subprocess.run(
                [
                    os.fspath(_OPENSSL),
                    "enc",
                    "-aes-256-cbc",
                    "-pbkdf2",
                    "-iter",
                    "600000",
                    "-md",
                    "sha256",
                    "-salt",
                    "-in",
                    os.fspath(temporary_tar),
                    "-out",
                    os.fspath(archive),
                    "-pass",
                    f"file:{key}",
                ],
                check=True,
                capture_output=True,
                timeout=3600,
            )
            os.chmod(archive, 0o400)
        except (OSError, subprocess.SubprocessError) as error:
            raise OpaqueBackupError("archive_encryption_failed") from error
        finally:
            if temporary_tar.exists():
                temporary_tar.unlink()

    @staticmethod
    def _restrict_clone(clone: Path) -> None:
        for path in sorted(clone.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
        os.chmod(clone, 0o700)

    @staticmethod
    def _seal_backup(backup: Path) -> None:
        for path in backup.rglob("*"):
            os.chmod(path, 0o500 if path.is_dir() else 0o400)
        os.chmod(backup, 0o500)

    def _verify_existing(
        self,
        paths: dict[str, Path],
        expected_source_path_sha256: str,
        expected_storage_attestation_sha256: str,
    ) -> OpaqueBackupResult:
        try:
            receipt_values = json.loads(paths["receipt"].read_text(encoding="utf-8"))
            receipt = OpaqueBackupReceipt(**receipt_values)
            encoded_key = paths["key"].read_bytes()
            if len(encoded_key) != 128:
                raise ValueError
            key_material = bytes.fromhex(encoded_key.decode("ascii"))
            if len(key_material) != 64:
                raise ValueError
            expected_receipt_hmac = hmac.new(key_material[:32], _receipt_payload(receipt), hashlib.sha256).hexdigest()
            archive_hmac = _hmac_file(paths["archive"], key_material[32:])
            clone_digest, clone_count, clone_bytes = _tree_digest(paths["clone"])
            backup_members = tuple((relative, is_directory) for _, relative, is_directory in _source_entries(paths["backup"]))
            recorded_archive_hmac = paths["archive_hmac"].read_text(encoding="ascii").strip()
            clear = all(
                (
                    hmac.compare_digest(expected_receipt_hmac, receipt.receipt_hmac_sha256),
                    hmac.compare_digest(receipt.source_path_sha256, expected_source_path_sha256),
                    hmac.compare_digest(receipt.storage_attestation_sha256, expected_storage_attestation_sha256),
                    hmac.compare_digest(_sha256_file(paths["archive"]), receipt.archive_sha256),
                    hmac.compare_digest(archive_hmac, receipt.archive_hmac_sha256),
                    hmac.compare_digest(recorded_archive_hmac, receipt.archive_hmac_sha256),
                    hmac.compare_digest(clone_digest, receipt.clone_tree_sha256),
                    clone_count == receipt.aggregate_file_count,
                    clone_bytes == receipt.aggregate_bytes,
                    stat.S_IMODE(paths["backup"].stat().st_mode) == 0o500,
                    stat.S_IMODE(paths["archive"].stat().st_mode) == 0o400,
                    stat.S_IMODE(paths["archive_hmac"].stat().st_mode) == 0o400,
                    stat.S_IMODE(paths["key"].stat().st_mode) == 0o400,
                    stat.S_IMODE(paths["receipt"].stat().st_mode) == 0o400,
                    backup_members == (("snapshot.enc", False), ("snapshot.hmac", False)),
                    _clone_permissions_clear(paths["clone"]),
                )
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise OpaqueBackupError("existing_snapshot_tampered") from error
        if not clear:
            raise OpaqueBackupError("existing_snapshot_tampered")
        return OpaqueBackupResult(receipt, paths["backup"], paths["archive"], paths["clone"], paths["key"], paths["receipt"])

    @staticmethod
    def _retain_failure(paths: dict[str, Path], failure_root: Path, error: Exception) -> None:
        try:
            _secure_mkdir(failure_root)
            if paths["staging"].exists():
                retained = failure_root / "retained-opaque-staging"
                if retained.exists():
                    raise OpaqueBackupError("failure_retention_collision")
                paths["staging"].rename(retained)
                OpaqueBackupEngine._restrict_clone(retained)
            code = str(error) if isinstance(error, OpaqueBackupError) else "opaque_backup_failed"
            _write_private_json(
                failure_root / "failure.json",
                {
                    "schema_version": "ik.opaque-continuity-failure.v1",
                    "status": "BLOCKED",
                    "failure_code": code,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception:
            # Never replace the original fail-closed result with cleanup detail.
            return
