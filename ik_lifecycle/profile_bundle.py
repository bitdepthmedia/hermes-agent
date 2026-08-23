"""Atomic Ernie primary/fast profile bundle with opaque local credentials."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

from .models import LifecycleBlockedError
from .opaque_backup import OpaqueBackupError, _clone_permissions_clear, _tree_digest
from .profile_candidate import _configure


@dataclass(frozen=True)
class ErnieProfileBundleInputs:
    primary: Path
    fast: Path
    router_credentials: Path
    compatibility_gateway_credentials: Path
    shared_credentials: Path
    router_port: int


@dataclass(frozen=True)
class ErnieProfileBundle:
    root: Path
    receipt: dict[str, object]


@dataclass(frozen=True)
class ProfileBundleValidation:
    status: str


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _source(path: Path, *, allowed_link_root: Path | None = None) -> tuple[Path, str]:
    root = Path(path).absolute()
    if (
        root.is_symlink()
        or not root.is_dir()
        or stat.S_IMODE(os.lstat(root).st_mode) != 0o700
    ):
        raise LifecycleBlockedError("profile_bundle_source_invalid", "profile bundle source is invalid")
    links = tuple(path for path in root.rglob("*") if path.is_symlink())
    if not links:
        try: clear = _clone_permissions_clear(root)
        except OpaqueBackupError as exc:
            raise LifecycleBlockedError("profile_bundle_source_invalid", "profile bundle source is invalid") from exc
        if not clear:
            raise LifecycleBlockedError("profile_bundle_source_invalid", "profile bundle source is invalid")
        return root, _tree_digest(root)[0]
    if allowed_link_root is None:
        raise LifecycleBlockedError("profile_bundle_symlink_invalid", "profile bundle symlink is not permitted")
    allowed = Path(allowed_link_root).resolve()
    digest = hashlib.sha256()
    for item in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        relative = item.relative_to(root).as_posix()
        if item.is_symlink():
            try:
                resolved = item.resolve(strict=True)
                resolved.relative_to(allowed)
            except (OSError, ValueError) as exc:
                raise LifecycleBlockedError(
                    "profile_bundle_symlink_invalid",
                    "profile bundle symlink target is not permitted",
                ) from exc
            if resolved.is_dir():
                value = _tree_digest(resolved)[0]
            elif resolved.is_file():
                value = hashlib.sha256(resolved.read_bytes()).hexdigest()
            else:
                raise LifecycleBlockedError(
                    "profile_bundle_symlink_invalid",
                    "profile bundle symlink target is invalid",
                )
            digest.update(f"L\0{relative}\0{value}\0".encode())
            continue
        expected = 0o700 if item.is_dir() else 0o600
        if stat.S_IMODE(os.lstat(item).st_mode) != expected:
            raise LifecycleBlockedError("profile_bundle_source_invalid", "profile bundle source permissions are invalid")
        if item.is_dir():
            digest.update(f"D\0{relative}\0".encode())
        else:
            digest.update(f"F\0{relative}\0".encode() + item.read_bytes() + b"\0")
    return root, digest.hexdigest()


def _credential(path: Path) -> tuple[Path, str]:
    source = Path(path).absolute()
    try: mode = source.stat().st_mode & 0o777
    except OSError as exc:
        raise LifecycleBlockedError("profile_bundle_credential_invalid", "opaque credential handle is invalid") from exc
    if source.is_symlink() or not source.is_file() or mode & 0o077 or source.stat().st_uid != os.getuid():
        raise LifecycleBlockedError("profile_bundle_credential_invalid", "opaque credential handle is invalid")
    return source, hashlib.sha256(source.read_bytes()).hexdigest()


def validate_ernie_profile_bundle(root: Path, receipt: dict[str, object]) -> ProfileBundleValidation:
    path = Path(root).absolute()
    try: clear = _clone_permissions_clear(path)
    except OpaqueBackupError as exc:
        raise LifecycleBlockedError("profile_bundle_invalid", "profile bundle changed") from exc
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    tree, count, total = _tree_digest(path)
    if (
        path.is_symlink()
        or not path.is_dir()
        or not clear
        or receipt.get("schema_id") != "ik.hermes.ernie-profile-bundle.v1"
        or receipt.get("status") != "CLEAR_PROFILE_BUNDLE"
        or receipt.get("bundle_tree_sha256") != tree
        or receipt.get("aggregate_file_count") != count
        or receipt.get("aggregate_bytes") != total
        or receipt.get("receipt_sha256") != hashlib.sha256(_canonical(body)).hexdigest()
    ):
        raise LifecycleBlockedError("profile_bundle_tampered", "profile bundle changed or was tampered")
    return ProfileBundleValidation("CLEAR")


def build_ernie_profile_bundle(inputs: ErnieProfileBundleInputs, destination: Path) -> ErnieProfileBundle:
    if not 1024 <= inputs.router_port <= 65535:
        raise LifecycleBlockedError("profile_bundle_endpoint_invalid", "profile bundle router endpoint is invalid")
    primary, primary_tree = _source(inputs.primary)
    fast, fast_tree = _source(inputs.fast, allowed_link_root=primary)
    router, router_sha = _credential(inputs.router_credentials)
    gateway, gateway_sha = _credential(inputs.compatibility_gateway_credentials)
    shared, shared_sha = _credential(inputs.shared_credentials)
    target = Path(destination).absolute()
    receipt_path = target.parent / f".{target.name}.receipt.json"
    source_binding = {
        "primary_tree_sha256": primary_tree,
        "fast_tree_sha256": fast_tree,
        "router_credential_sha256": router_sha,
        "compatibility_gateway_credential_sha256": gateway_sha,
        "shared_credential_sha256": shared_sha,
        "router_port": inputs.router_port,
    }
    if target.exists() or target.is_symlink():
        try: receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LifecycleBlockedError("profile_bundle_exists", "profile bundle already exists without a valid receipt") from exc
        validate_ernie_profile_bundle(target, receipt)
        if receipt.get("source_binding") != source_binding:
            raise LifecycleBlockedError("profile_bundle_source_drift", "profile bundle source binding changed")
        return ErnieProfileBundle(target, receipt)
    if target.parent.is_symlink():
        raise LifecycleBlockedError("profile_bundle_parent_invalid", "profile bundle parent is a symlink")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = target.with_name(f".{target.name}.{os.getpid()}.staging")
    try:
        staging.mkdir(mode=0o700)
        shutil.copytree(primary, staging / "primary", symlinks=False)
        shutil.copytree(fast, staging / "fast", symlinks=False)
        _configure(staging / "primary", inputs.router_port)
        _configure(staging / "fast", inputs.router_port)
        (staging / "router").mkdir(mode=0o700)
        shutil.copy2(router, staging / "router/.env")
        (staging / "compatibility-gateway").mkdir(mode=0o700)
        shutil.copy2(gateway, staging / "compatibility-gateway/.env")
        shutil.copy2(shared, staging / "compatibility-gateway/shared-core.env")
        for item in sorted(staging.rglob("*"), key=lambda value: len(value.parts), reverse=True):
            if item.is_symlink(): raise LifecycleBlockedError("profile_bundle_symlink", "profile bundle contains a symlink")
            os.chmod(item, 0o700 if item.is_dir() else 0o600)
        tree, count, total = _tree_digest(staging)
        body = {
            "schema_id": "ik.hermes.ernie-profile-bundle.v1",
            "status": "CLEAR_PROFILE_BUNDLE",
            "source_binding": source_binding,
            "bundle_tree_sha256": tree,
            "aggregate_file_count": count,
            "aggregate_bytes": total,
            "profiles": ["primary", "fast"],
            "credential_classes": ["router", "compatibility-gateway", "shared-core"],
            "model": "ik-qwen38-eval:31629f53165a",
            "provider": "ik-ernie-local",
            "keyword_routing": False,
        }
        receipt = {**body, "receipt_sha256": hashlib.sha256(_canonical(body)).hexdigest()}
        os.replace(staging, target)
        receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.chmod(receipt_path, 0o600)
        validate_ernie_profile_bundle(target, receipt)
        return ErnieProfileBundle(target, receipt)
    except Exception:
        if staging.exists():
            failed = staging.with_name(staging.name + ".failed")
            try: os.replace(staging, failed)
            except OSError: pass
        raise
