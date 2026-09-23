"""Immutable dependency and disposable-cache contract for corrected execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .audited_dependencies import dependency_tree_digest
from .models import LifecycleBlockedError


SCHEMA_ID = "ik.hermes.composed-execution-correction.v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def bind_correction_contract(source: Mapping[str, Any]) -> dict[str, Any]:
    contract = json.loads(json.dumps(source))
    contract.pop("contract_sha256", None)
    contract["contract_sha256"] = hashlib.sha256(_canonical(contract)).hexdigest()
    return contract


def _read_sha256(path: Path, code: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise LifecycleBlockedError(code, "bound correction artifact is unavailable") from exc


def validate_correction_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Prove copied dependencies are immutable and caches start disposable/empty."""

    if contract.get("schema_id") != SCHEMA_ID:
        raise LifecycleBlockedError("corrected_contract_schema_invalid", "unknown correction contract schema")
    unsigned = {key: value for key, value in contract.items() if key != "contract_sha256"}
    if contract.get("contract_sha256") != hashlib.sha256(_canonical(unsigned)).hexdigest():
        raise LifecycleBlockedError("corrected_contract_digest_invalid", "correction contract digest changed")
    build_root = Path(str(contract.get("build_root", "")))
    cache_root = Path(str(contract.get("disposable_cache_root", "")))
    if not build_root.is_absolute() or not cache_root.is_absolute() or _inside(cache_root, build_root) or _inside(build_root, cache_root):
        raise LifecycleBlockedError("corrected_cache_path_invalid", "disposable caches must be outside the composed build root")
    dependencies = contract.get("dependency_surfaces")
    caches = contract.get("caches")
    configs = contract.get("vite_configs")
    if not isinstance(dependencies, list) or not dependencies or not isinstance(caches, list) or not caches or not isinstance(configs, list) or len(configs) != len(caches):
        raise LifecycleBlockedError("corrected_contract_incomplete", "correction dependency/cache bindings are incomplete")
    dependency_paths: list[Path] = []
    for surface in dependencies:
        path = Path(str(surface.get("path", "")))
        if not path.is_absolute() or not _inside(path, build_root) or path.name != "node_modules":
            raise LifecycleBlockedError("corrected_dependency_path_invalid", "dependency surface path is invalid")
        if surface.get("retention") != "immutable_fail_closed" or dependency_tree_digest(path) != surface.get("sha256"):
            raise LifecycleBlockedError("corrected_dependency_surface_drift", "copied dependency surface changed")
        dependency_paths.append(path)
    cache_ids: set[str] = set()
    for cache in caches:
        cache_id = cache.get("cache_id")
        path = Path(str(cache.get("path", "")))
        if not isinstance(cache_id, str) or cache_id in cache_ids or not path.is_absolute() or not _inside(path, cache_root):
            raise LifecycleBlockedError("corrected_cache_path_invalid", "cache path is not uniquely confined")
        if any(_inside(path, dependency) or _inside(dependency, path) for dependency in dependency_paths):
            raise LifecycleBlockedError("corrected_cache_path_invalid", "cache overlaps an immutable dependency surface")
        if cache.get("retention") != "retain_on_failure_discard_after_clear_receipt" or dependency_tree_digest(path) != cache.get("pre_sha256"):
            raise LifecycleBlockedError("corrected_cache_prestate_drift", "disposable cache prestate changed")
        cache_ids.add(cache_id)
    for config in configs:
        path = Path(str(config.get("path", "")))
        if config.get("cache_id") not in cache_ids or not path.is_absolute() or not _inside(path, build_root):
            raise LifecycleBlockedError("corrected_vite_config_invalid", "Vite config binding is invalid")
        if _read_sha256(path, "corrected_vite_config_invalid") != config.get("sha256"):
            raise LifecycleBlockedError("corrected_vite_config_invalid", "Vite config digest changed")
    if contract.get("rerun_decision") != "rerun_all_commands_in_new_composition":
        raise LifecycleBlockedError("corrected_rerun_decision_invalid", "correction must use a fresh full batch")
    return {"status": "CLEAR", "dependency_count": len(dependencies), "cache_count": len(caches), "contract_sha256": contract["contract_sha256"]}
