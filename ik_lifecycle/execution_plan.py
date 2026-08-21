"""Digest binding and fail-closed validation for candidate execution plans."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .models import LifecycleBlockedError


SCHEMA_ID = "ik.hermes.candidate-execution-plan.v1"
FORBIDDEN_COMMAND_TOKENS = (
    "axios@1.14.1",
    "axios@0.30.4",
    "plain-crypto-js@4.2.1",
)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _unsigned(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def bind_execution_plan(source: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy with deterministic command-list and plan digests."""

    plan = json.loads(json.dumps(source))
    command_digests: list[str] = []
    for batch in plan.get("batches", []):
        for command in batch.get("commands", []):
            command["command_sha256"] = _digest(_unsigned(command, "command_sha256"))
            command_digests.append(command["command_sha256"])
    plan["command_count"] = len(command_digests)
    plan["commands_sha256"] = _digest(command_digests)
    plan["plan_sha256"] = _digest(_unsigned(plan, "plan_sha256"))
    return plan


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _validate_command_tokens(command: Mapping[str, Any]) -> None:
    values = [str(item) for item in command.get("argv", [])]
    values.extend(f"{key}={value}" for key, value in command.get("env", {}).items())
    for value in values:
        lowered = value.lower()
        if any(token in lowered for token in FORBIDDEN_COMMAND_TOKENS):
            raise LifecycleBlockedError(
                "execution_plan_supply_chain_invalid",
                "A planned command contains a forbidden package version",
            )
        if lowered == "latest" or "@latest" in lowered:
            raise LifecycleBlockedError(
                "execution_plan_supply_chain_invalid",
                "A planned command contains a floating latest selector",
            )


def _validate_command_environment(command: Mapping[str, Any], execution_root: Path) -> None:
    argv = command.get("argv", [])
    if not any(Path(item).name == "npm" for item in argv):
        return
    env = command.get("env")
    required = {
        "HOME": None,
        "PATH": None,
        "LANG": None,
        "TZ": None,
        "CI": "1",
        "NPM_CONFIG_OFFLINE": "true",
        "NPM_CONFIG_IGNORE_SCRIPTS": "true",
        "NPM_CONFIG_AUDIT": "false",
        "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_UPDATE_NOTIFIER": "false",
        "NPM_CONFIG_CACHE": None,
        "NPM_CONFIG_USERCONFIG": None,
        "NPM_CONFIG_GLOBALCONFIG": None,
    }
    if command.get("environment_mode") != "replace" or not isinstance(env, Mapping):
        raise LifecycleBlockedError(
            "execution_plan_environment_invalid",
            "npm commands require a replacement environment",
        )
    if any(key not in env or (value is not None and env.get(key) != value) for key, value in required.items()):
        raise LifecycleBlockedError(
            "execution_plan_environment_invalid",
            "npm command environment is incomplete",
        )
    user_config = Path(str(env["NPM_CONFIG_USERCONFIG"]))
    global_config = Path(str(env["NPM_CONFIG_GLOBALCONFIG"]))
    cache = Path(str(env["NPM_CONFIG_CACHE"]))
    if (
        user_config == global_config
        or not user_config.is_absolute()
        or not global_config.is_absolute()
        or not cache.is_absolute()
        or not _inside(user_config, execution_root)
        or not _inside(global_config, execution_root)
        or not _inside(cache, execution_root)
    ):
        raise LifecycleBlockedError(
            "execution_plan_environment_invalid",
            "npm config and cache paths must be distinct, absolute and confined",
        )


def validate_execution_plan(
    plan: Mapping[str, Any],
    *,
    candidate_id: str | None = None,
    target_commit_sha: str | None = None,
    source_tree_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate identity, authority, path and digest bindings without executing it."""

    if plan.get("schema_id") != SCHEMA_ID:
        raise LifecycleBlockedError("execution_plan_schema_invalid", "Unknown candidate execution-plan schema")
    if plan.get("plan_sha256") != _digest(_unsigned(plan, "plan_sha256")):
        raise LifecycleBlockedError("execution_plan_digest_invalid", "Candidate execution-plan digest is invalid")

    candidate = plan.get("candidate")
    if not isinstance(candidate, Mapping):
        raise LifecycleBlockedError("execution_plan_candidate_mismatch", "Candidate binding is missing")
    expected = {
        "candidate_id": candidate_id,
        "target_commit_sha": target_commit_sha,
        "source_tree_sha256": source_tree_sha256,
    }
    for key, value in expected.items():
        if value is not None and candidate.get(key) != value:
            raise LifecycleBlockedError("execution_plan_candidate_mismatch", f"Candidate {key} binding changed")

    immutable_source = Path(str(candidate.get("immutable_source", "")))
    execution_root = Path(str(candidate.get("execution_root", "")))
    if not immutable_source.is_absolute() or not execution_root.is_absolute():
        raise LifecycleBlockedError("execution_plan_path_invalid", "Candidate paths must be absolute")
    protected = [Path(str(item)) for item in plan.get("protected_paths", [])]

    command_ids: set[str] = set()
    command_digests: list[str] = []
    for batch in plan.get("batches", []):
        for command in batch.get("commands", []):
            command_id = command.get("command_id")
            if not isinstance(command_id, str) or not command_id or command_id in command_ids:
                raise LifecycleBlockedError("execution_plan_command_invalid", "Command ids must be unique")
            command_ids.add(command_id)
            argv = command.get("argv")
            if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
                raise LifecycleBlockedError("execution_plan_command_invalid", f"Command {command_id} has invalid argv")
            workdir = Path(str(command.get("workdir", "")))
            if not workdir.is_absolute() or not _inside(workdir, execution_root):
                raise LifecycleBlockedError("execution_plan_path_invalid", f"Command {command_id} leaves its execution root")
            if _inside(workdir, immutable_source) or any(_inside(workdir, root) for root in protected):
                raise LifecycleBlockedError("execution_plan_path_invalid", f"Command {command_id} targets a protected path")
            _validate_command_tokens(command)
            _validate_command_environment(command, execution_root)
            expected_digest = _digest(_unsigned(command, "command_sha256"))
            if command.get("command_sha256") != expected_digest:
                raise LifecycleBlockedError("execution_plan_digest_invalid", f"Command {command_id} digest is invalid")
            command_digests.append(expected_digest)

    if plan.get("command_count") != len(command_digests) or plan.get("commands_sha256") != _digest(command_digests):
        raise LifecycleBlockedError("execution_plan_digest_invalid", "Command-list digest is invalid")

    blockers = plan.get("blockers")
    executable = plan.get("authorization", {}).get("executable")
    if plan.get("status") == "BLOCKED_PREREQUISITES":
        if not isinstance(blockers, list) or not blockers or executable is not False:
            raise LifecycleBlockedError(
                "execution_plan_authority_invalid",
                "A prerequisite-blocked plan cannot grant execution authority",
            )
    elif plan.get("status") == "APPROVAL_REQUIRED":
        if blockers or executable is not False:
            raise LifecycleBlockedError(
                "execution_plan_authority_invalid",
                "An unapproved plan cannot grant execution authority",
            )
    else:
        raise LifecycleBlockedError("execution_plan_authority_invalid", "Unsupported execution-plan status")

    return {
        "plan_sha256": plan["plan_sha256"],
        "commands_sha256": plan["commands_sha256"],
        "command_count": len(command_digests),
        "executable": False,
    }
