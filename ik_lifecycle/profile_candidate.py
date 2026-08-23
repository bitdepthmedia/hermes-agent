"""Build a private Ernie profile candidate from a validated migration clone."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat

import yaml

from .models import LifecycleBlockedError
from .opaque_backup import OpaqueBackupError, _clone_permissions_clear, _tree_digest


_MODEL = "ik-qwen38-eval:31629f53165a"
_PLUGIN = "ik-persona-orchestration"
_CONTEXT_LENGTH = 65_536


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _configure(path: Path, model_port: int) -> None:
    if not 1024 <= model_port <= 65535:
        raise LifecycleBlockedError("profile_model_endpoint_invalid", "profile model endpoint is invalid")
    config_path = path / "config.yaml"
    try:
        config = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise LifecycleBlockedError("profile_config_invalid", "profile configuration is invalid") from exc
    if not isinstance(config, dict):
        raise LifecycleBlockedError("profile_config_invalid", "profile configuration is invalid")
    config["model"] = {
        "provider": "ik-ernie-local",
        "default": _MODEL,
        "model": _MODEL,
        "base_url": f"http://127.0.0.1:{model_port}/v1",
        "api_mode": "chat_completions",
        # Qwen3.8 advertises 262K, but 64K is the reviewed live operating cap:
        # large enough for Hermes' agentic floor while retaining memory headroom.
        "context_length": _CONTEXT_LENGTH,
    }
    agent = config.get("agent")
    if not isinstance(agent, dict):
        agent = {}
    # The legacy router used ``none`` as a budget guardrail.  Qwen3.8 was
    # validated with capability-aware reasoning, so the rebuilt cell makes
    # that intent explicit instead of inheriting the stale profile value.
    agent["reasoning_effort"] = "medium"
    config["agent"] = agent
    routing = config.get("smart_model_routing")
    if not isinstance(routing, dict):
        routing = {}
    routing["enabled"] = False
    config["smart_model_routing"] = routing
    plugins = config.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
    enabled = plugins.get("enabled")
    if not isinstance(enabled, list) or any(not isinstance(item, str) for item in enabled):
        enabled = []
    plugins["enabled"] = [*dict.fromkeys((*enabled, _PLUGIN))]
    config["plugins"] = plugins
    temporary = config_path.with_name(f".{config_path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        yaml.safe_dump(config, stream, sort_keys=True)
        stream.flush(); os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, config_path)


def _receipt(root: Path, source_tree: str) -> dict[str, object]:
    tree, count, total = _tree_digest(root)
    body = {
        "schema_id": "ik.hermes.ernie-profile-candidate.v1",
        "status": "CLEAR_PROFILE_CANDIDATE",
        "source_tree_sha256": source_tree,
        "candidate_tree_sha256": tree,
        "aggregate_file_count": count,
        "aggregate_bytes": total,
        "model": _MODEL,
        "provider": "ik-ernie-local",
        "plugin": _PLUGIN,
        "keyword_routing": False,
    }
    return {**body, "receipt_sha256": hashlib.sha256(_canonical(body)).hexdigest()}


def validate_ernie_profile_candidate(root: Path, receipt: dict[str, object]) -> str:
    path = Path(root).absolute()
    if path.is_symlink() or not path.is_dir() or not _clone_permissions_clear(path):
        raise LifecycleBlockedError("profile_candidate_invalid", "profile candidate is invalid")
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    tree, count, total = _tree_digest(path)
    if (
        receipt.get("schema_id") != "ik.hermes.ernie-profile-candidate.v1"
        or receipt.get("status") != "CLEAR_PROFILE_CANDIDATE"
        or receipt.get("receipt_sha256") != hashlib.sha256(_canonical(body)).hexdigest()
        or receipt.get("candidate_tree_sha256") != tree
        or receipt.get("aggregate_file_count") != count
        or receipt.get("aggregate_bytes") != total
    ):
        raise LifecycleBlockedError("profile_candidate_tampered", "profile candidate changed")
    return "CLEAR"


def build_ernie_profile_candidate(source: Path, destination: Path, *, model_port: int) -> dict[str, object]:
    origin = Path(source).absolute()
    target = Path(destination).absolute()
    try:
        source_clear = _clone_permissions_clear(origin)
    except OpaqueBackupError as exc:
        raise LifecycleBlockedError("profile_source_invalid", "migration clone is invalid") from exc
    if origin.is_symlink() or not origin.is_dir() or not source_clear:
        raise LifecycleBlockedError("profile_source_invalid", "migration clone is invalid")
    source_tree, _, _ = _tree_digest(origin)
    if target.exists() or target.is_symlink():
        receipt_path = target.parent / f".{target.name}.receipt.json"
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LifecycleBlockedError("profile_candidate_exists", "profile candidate already exists") from exc
        validate_ernie_profile_candidate(target, receipt)
        if receipt.get("source_tree_sha256") != source_tree:
            raise LifecycleBlockedError("profile_candidate_source_drift", "profile candidate source changed")
        return receipt
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.parent.resolve() != target.parent:
        raise LifecycleBlockedError("profile_candidate_parent_invalid", "profile candidate parent uses a symlink")
    staging = target.with_name(f".{target.name}.{os.getpid()}.staging")
    try:
        shutil.copytree(origin, staging, symlinks=False)
        _configure(staging, model_port)
        for item in sorted(staging.rglob("*"), key=lambda value: len(value.parts), reverse=True):
            if item.is_symlink():
                raise LifecycleBlockedError("profile_candidate_symlink", "profile candidate contains a symlink")
            os.chmod(item, 0o700 if item.is_dir() else 0o600)
        os.chmod(staging, 0o700)
        receipt = _receipt(staging, source_tree)
        os.replace(staging, target)
        receipt_path = target.parent / f".{target.name}.receipt.json"
        receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.chmod(receipt_path, 0o600)
        validate_ernie_profile_candidate(target, receipt)
        return receipt
    except Exception:
        # Preserve failed private candidates for local forensic review; never serialize paths.
        if staging.exists():
            failed = staging.with_name(staging.name + ".failed")
            try: os.replace(staging, failed)
            except OSError: pass
        raise
