"""Digest binding and fail-closed validation for candidate execution plans."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .composed_source import tree_digest
from .models import LifecycleBlockedError
from .network_guard import MACOS_DENY_NETWORK_POLICY, validate_network_proof


SCHEMA_ID = "ik.hermes.candidate-execution-plan.v1"
COMPOSED_SCHEMA_ID = "ik.hermes.composed-execution-plan.v2"
APPROVAL_SCHEMA_ID = "ik.hermes.execution-approval.v1"
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


def bind_execution_approval(source: Mapping[str, Any]) -> dict[str, Any]:
    """Bind an external approval to one exact plan and ordered command set."""

    approval = json.loads(json.dumps(source))
    approval["approval_sha256"] = _digest(_unsigned(approval, "approval_sha256"))
    return approval


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


def _file_sha256(path: Path, code: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise LifecycleBlockedError(code, "bound execution artifact is unavailable") from exc


def validate_composed_execution_plan(
    plan: Mapping[str, Any],
    *,
    implementation_commit: str | None = None,
) -> dict[str, Any]:
    """Validate a post-overlay plan against the pristine composed tree."""

    if plan.get("schema_id") != COMPOSED_SCHEMA_ID:
        raise LifecycleBlockedError(
            "composed_execution_plan_schema_invalid",
            "only a composed-source execution plan can authorize this phase",
        )
    if plan.get("plan_sha256") != _digest(_unsigned(plan, "plan_sha256")):
        raise LifecycleBlockedError("execution_plan_digest_invalid", "Composed execution-plan digest is invalid")
    if implementation_commit is not None and plan.get("implementation_commit") != implementation_commit:
        raise LifecycleBlockedError("composed_execution_plan_implementation_drift", "lifecycle implementation commit changed")

    # Reuse all v1 command, path, authority, environment and supply-chain gates.
    common = json.loads(json.dumps(plan))
    common["schema_id"] = SCHEMA_ID
    common = bind_execution_plan(common)
    validate_execution_plan(common)

    composition = plan.get("composition")
    candidate = plan.get("candidate")
    if not isinstance(composition, Mapping) or not isinstance(candidate, Mapping):
        raise LifecycleBlockedError("composed_execution_plan_binding_invalid", "composed candidate binding is missing")
    immutable = Path(str(composition.get("immutable_source", "")))
    build_root = Path(str(composition.get("build_root", "")))
    manifest_path = Path(str(composition.get("manifest_path", "")))
    if (
        not immutable.is_absolute()
        or not build_root.is_absolute()
        or not manifest_path.is_absolute()
        or candidate.get("immutable_source") != str(immutable)
        or candidate.get("execution_root") != str(build_root)
    ):
        raise LifecycleBlockedError("composed_execution_plan_binding_invalid", "composed paths are inconsistent")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleBlockedError("composed_execution_plan_binding_invalid", "composition manifest is unreadable") from exc
    expected_tree = composition.get("composed_tree_sha256")
    if (
        manifest.get("composition_id") != composition.get("composition_id")
        or manifest.get("composed_tree_sha256") != expected_tree
        or manifest.get("build_root_pristine_sha256") != composition.get("build_root_pristine_sha256")
    ):
        raise LifecycleBlockedError("composed_execution_plan_binding_invalid", "composition manifest binding changed")
    if tree_digest(immutable) != expected_tree or tree_digest(build_root, excluded_names=("node_modules",)) != composition.get("build_root_pristine_sha256"):
        raise LifecycleBlockedError("composed_execution_plan_tree_drift", "composed or pristine build tree changed")

    isolation = plan.get("network_isolation")
    if not isinstance(isolation, Mapping):
        raise LifecycleBlockedError("execution_network_contract_invalid", "network isolation contract is missing")
    runtime = Path(str(isolation.get("runtime", "")))
    sandbox_exec = Path("/usr/bin/sandbox-exec")
    from . import network_guard

    expected_bindings = {
        "policy_sha256": hashlib.sha256(MACOS_DENY_NETWORK_POLICY.encode("utf-8")).hexdigest(),
        "adapter_sha256": _file_sha256(Path(network_guard.__file__), "execution_network_contract_invalid"),
        "runtime_sha256": _file_sha256(runtime, "execution_network_contract_invalid"),
        "sandbox_exec_sha256": _file_sha256(sandbox_exec, "execution_network_contract_invalid"),
    }
    if isolation.get("policy") != MACOS_DENY_NETWORK_POLICY:
        raise LifecycleBlockedError("execution_network_contract_invalid", "network isolation policy changed")
    if any(isolation.get(key) != value for key, value in expected_bindings.items()):
        raise LifecycleBlockedError("execution_network_contract_invalid", "network isolation binding changed")
    ttl = isolation.get("proof_ttl_seconds")
    if not isinstance(ttl, int) or ttl < 1 or ttl > 900:
        raise LifecycleBlockedError("execution_network_contract_invalid", "network proof TTL is invalid")
    return {
        "plan_sha256": plan["plan_sha256"],
        "commands_sha256": plan["commands_sha256"],
        "command_count": plan["command_count"],
        "composition_id": composition["composition_id"],
        "executable": False,
    }


def _approval_time(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        raise LifecycleBlockedError(code, "execution approval timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LifecycleBlockedError(code, "execution approval timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise LifecycleBlockedError(code, "execution approval timestamp requires a timezone")
    return parsed.astimezone(timezone.utc)


def validate_execution_authorization(
    plan: Mapping[str, Any],
    approval: Mapping[str, Any] | None,
    *,
    proof_path: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return an exact non-executing authorization record after every gate."""

    validate_composed_execution_plan(plan)
    if approval is None:
        raise LifecycleBlockedError("execution_approval_missing", "separate exact execution approval is required")
    if approval.get("schema_id") != APPROVAL_SCHEMA_ID or approval.get("status") != "APPROVED":
        raise LifecycleBlockedError("execution_approval_invalid", "execution approval is not APPROVED")
    if approval.get("approval_sha256") != _digest(_unsigned(approval, "approval_sha256")):
        raise LifecycleBlockedError("execution_approval_digest_invalid", "execution approval digest is invalid")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current < _approval_time(approval.get("approved_at"), "execution_approval_invalid") or current > _approval_time(approval.get("expires_at"), "execution_approval_stale"):
        raise LifecycleBlockedError("execution_approval_stale", "execution approval is outside its validity window")
    command_digests = [
        command["command_sha256"]
        for batch in plan.get("batches", [])
        for command in batch.get("commands", [])
    ]
    if (
        approval.get("plan_sha256") != plan.get("plan_sha256")
        or approval.get("commands_sha256") != plan.get("commands_sha256")
        or approval.get("command_digests") != command_digests
        or approval.get("scope") != "exact_composed_candidate_batch"
    ):
        raise LifecycleBlockedError("execution_approval_mismatch", "execution approval does not bind the exact plan")
    isolation = plan["network_isolation"]
    proof = validate_network_proof(
        proof_path,
        runtime=Path(isolation["runtime"]),
        now=current,
    )
    proof_bindings = proof["bindings"]
    for key in ("adapter_sha256", "policy_sha256", "runtime_sha256", "sandbox_exec_sha256"):
        if proof_bindings.get(key) != isolation.get(key):
            raise LifecycleBlockedError("execution_network_proof_mismatch", "fresh network proof does not match the plan")
    if proof.get("adapter_id") != isolation.get("adapter_id") or proof.get("ttl_seconds") != isolation.get("proof_ttl_seconds"):
        raise LifecycleBlockedError("execution_network_proof_mismatch", "fresh network proof contract changed")
    return {
        "authorized": True,
        "plan_sha256": plan["plan_sha256"],
        "commands_sha256": plan["commands_sha256"],
        "command_digests": command_digests,
        "network_proof_sha256": proof["proof_sha256"],
    }
