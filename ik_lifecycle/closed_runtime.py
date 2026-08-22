"""Credential-bound, public-synthetic Ernie closed-runtime contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from typing import Mapping, Sequence

import yaml


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_ENV_KEY = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
_HANDLE_CLASSES = ("ernie_profile_secret_bundle", "nate_os_local_agent_identity")
_OPERATIONS = (
    "rebind-immutable-inputs",
    "resolve-opaque-handles",
    "fresh-network-proof",
    "start-loopback-model-worker",
    "start-isolated-ernie-cell",
    "run-public-synthetic-gates",
    "verify-zero-private-exposure",
    "stop-isolated-cell",
    "rehearse-rp2-rp3-rollback",
)
_CONTROL_ENV_KEYS = frozenset(
    {
        "BASH_ENV",
        "ENV",
        "HOME",
        "HERMES_HOME",
        "LD_LIBRARY_PATH",
        "NODE_OPTIONS",
        "NO_PROXY",
        "OLLAMA_HOST",
        "OLLAMA_MODELS",
        "PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONSTARTUP",
    }
)
_CONTROL_ENV_PREFIXES = ("DYLD_", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")
_PRIVATE_KEYS = frozenset(
    {
        "api_key",
        "credential_value",
        "password",
        "path",
        "private_content",
        "private_prompt",
        "raw",
        "raw_path",
        "raw_value",
        "secret_value",
        "token",
        "value",
    }
)


class ClosedRuntimeError(RuntimeError):
    """A deliberately non-sensitive closed-runtime error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _is_hex(value: object, *, length: int = 64) -> bool:
    return isinstance(value, str) and (_HEX64 if length == 64 else _HEX40).fullmatch(value) is not None


def _safe_regular_file(path: Path, *, secret: bool) -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise ClosedRuntimeError("opaque_handle_missing") from error
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or mode & 0o022
        or (secret and mode & 0o077)
    ):
        raise ClosedRuntimeError("opaque_handle_permissions_invalid")
    return metadata


def _parse_secret_environment(path: Path) -> tuple[dict[str, str], bytes, os.stat_result]:
    metadata = _safe_regular_file(path, secret=True)
    try:
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise ClosedRuntimeError("opaque_secret_bundle_invalid") from error
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ClosedRuntimeError("opaque_secret_bundle_invalid")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not _ENV_KEY.fullmatch(key):
            raise ClosedRuntimeError("opaque_secret_bundle_invalid")
        if key in _CONTROL_ENV_KEYS or any(key.startswith(prefix) for prefix in _CONTROL_ENV_PREFIXES):
            raise ClosedRuntimeError("opaque_secret_control_key_rejected")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if not value or "\x00" in value:
            raise ClosedRuntimeError("opaque_secret_bundle_invalid")
        if key in values:
            if not hmac.compare_digest(values[key], value):
                raise ClosedRuntimeError("opaque_secret_bundle_invalid")
            continue
        values[key] = value
    if not values:
        raise ClosedRuntimeError("opaque_secret_bundle_invalid")
    return values, raw, metadata


def _parse_nate_identity(path: Path) -> tuple[str, bytes, os.stat_result]:
    metadata = _safe_regular_file(path, secret=False)
    try:
        raw = path.read_bytes()
        document = yaml.safe_load(raw.decode("utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ClosedRuntimeError("opaque_identity_invalid") from error
    servers = document.get("mcp_servers") if isinstance(document, Mapping) else None
    if not isinstance(servers, Mapping):
        raise ClosedRuntimeError("opaque_identity_invalid")
    candidates = [value for key, value in servers.items() if str(key).lower().replace("-", "_") == "nate_os"]
    if len(candidates) != 1 or not isinstance(candidates[0], Mapping):
        raise ClosedRuntimeError("opaque_identity_ambiguous")
    arguments = candidates[0].get("args")
    if not isinstance(arguments, Sequence) or isinstance(arguments, (str, bytes)):
        raise ClosedRuntimeError("opaque_identity_invalid")
    positions = [index for index, value in enumerate(arguments) if value == "--agent-id"]
    if len(positions) != 1 or positions[0] + 1 >= len(arguments):
        raise ClosedRuntimeError("opaque_identity_ambiguous")
    identity = arguments[positions[0] + 1]
    if not isinstance(identity, str) or not identity or identity.startswith("-") or "\x00" in identity:
        raise ClosedRuntimeError("opaque_identity_invalid")
    return identity, raw, metadata


def _binding(key: bytes, class_name: str, raw: bytes, metadata: os.stat_result) -> str:
    material = b"\0".join(
        (
            class_name.encode("ascii"),
            str(metadata.st_uid).encode("ascii"),
            str(stat.S_IMODE(metadata.st_mode)).encode("ascii"),
            str(metadata.st_size).encode("ascii"),
            raw,
        )
    )
    return hmac.new(key, material, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class OpaqueHandleBinding:
    class_name: str
    binding_hmac_sha256: str
    entry_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "class": self.class_name,
            "binding_hmac_sha256": self.binding_hmac_sha256,
            "entry_count": self.entry_count,
            "resolved": True,
        }


@dataclass(repr=False)
class ResolvedOpaqueHandles:
    _secret_path: Path = field(repr=False)
    _config_path: Path = field(repr=False)
    _hmac_key: bytes = field(repr=False)
    _secret_environment: dict[str, str] = field(repr=False)
    _identity: str = field(repr=False)
    bindings: tuple[OpaqueHandleBinding, OpaqueHandleBinding]

    def __repr__(self) -> str:
        return "ResolvedOpaqueHandles(classes=2, values=<opaque>)"

    def safe_receipt(self) -> dict[str, object]:
        return {
            "schema_id": "ik.hermes.opaque-runtime-handles.v1",
            "status": "CLEAR",
            "handles": [binding.to_dict() for binding in self.bindings],
        }

    def validate_unchanged(self) -> None:
        environment, secret_raw, secret_metadata = _parse_secret_environment(self._secret_path)
        identity, config_raw, config_metadata = _parse_nate_identity(self._config_path)
        expected = (
            _binding(self._hmac_key, _HANDLE_CLASSES[0], secret_raw, secret_metadata),
            _binding(self._hmac_key, _HANDLE_CLASSES[1], config_raw + b"\0" + identity.encode("utf-8"), config_metadata),
        )
        if (
            tuple(binding.binding_hmac_sha256 for binding in self.bindings) != expected
            or environment != self._secret_environment
            or not hmac.compare_digest(identity, self._identity)
        ):
            raise ClosedRuntimeError("opaque_handle_drift")

    def materialize_environment(self, base: Mapping[str, str]) -> dict[str, str]:
        self.validate_unchanged()
        environment = {str(key): str(value) for key, value in base.items()}
        additions = {**self._secret_environment, "NATE_OS_AGENT_ID": self._identity}
        for key, value in additions.items():
            if key in environment and environment[key] != value:
                raise ClosedRuntimeError("opaque_handle_environment_collision")
            environment[key] = value
        return environment

    def secret_leak_count(self, log_paths: Sequence[Path]) -> int:
        needles = tuple(value.encode("utf-8") for value in (*self._secret_environment.values(), self._identity) if value)
        leaked = 0
        for path in log_paths:
            try:
                payload = Path(path).read_bytes()
            except OSError as error:
                raise ClosedRuntimeError("closed_runtime_log_unavailable") from error
            if any(needle in payload for needle in needles):
                leaked += 1
        return leaked


def resolve_opaque_handles(profile_root: Path, *, hmac_key: bytes) -> ResolvedOpaqueHandles:
    """Resolve exact local sources while exposing only keyed bindings."""

    root = Path(profile_root).absolute()
    try:
        root_metadata = os.lstat(root)
    except OSError as error:
        raise ClosedRuntimeError("opaque_profile_invalid") from error
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode) or root_metadata.st_uid != os.getuid():
        raise ClosedRuntimeError("opaque_profile_invalid")
    if not isinstance(hmac_key, bytes) or len(hmac_key) != 32:
        raise ClosedRuntimeError("opaque_handle_key_invalid")
    secret_path = root / ".env"
    config_path = root / "config.yaml"
    environment, secret_raw, secret_metadata = _parse_secret_environment(secret_path)
    identity, config_raw, config_metadata = _parse_nate_identity(config_path)
    bindings = (
        OpaqueHandleBinding(
            _HANDLE_CLASSES[0],
            _binding(hmac_key, _HANDLE_CLASSES[0], secret_raw, secret_metadata),
            len(environment),
        ),
        OpaqueHandleBinding(
            _HANDLE_CLASSES[1],
            _binding(hmac_key, _HANDLE_CLASSES[1], config_raw + b"\0" + identity.encode("utf-8"), config_metadata),
            1,
        ),
    )
    return ResolvedOpaqueHandles(secret_path, config_path, hmac_key, environment, identity, bindings)


def build_execution_approval(
    *,
    plan_sha256: str,
    selection_sha256: str,
    implementation_commit: str,
    executor_sha256: str,
    module_sha256: str,
    overlay_manifest_sha256: str,
) -> dict[str, object]:
    if (
        not _is_hex(plan_sha256)
        or not _is_hex(selection_sha256)
        or not _is_hex(implementation_commit, length=40)
        or not all(_is_hex(value) for value in (executor_sha256, module_sha256, overlay_manifest_sha256))
    ):
        raise ClosedRuntimeError("closed_runtime_approval_binding_invalid")
    body: dict[str, object] = {
        "schema_id": "ik.hermes.credential-bound-closed-runtime-approval.v1",
        "status": "APPROVED",
        "authority": "current_user_instruction",
        "plan_sha256": plan_sha256,
        "selection_sha256": selection_sha256,
        "implementation_commit": implementation_commit,
        "execution_bindings": {
            "executor_sha256": executor_sha256,
            "module_sha256": module_sha256,
            "overlay_manifest_sha256": overlay_manifest_sha256,
        },
        "ordered_operations": list(_OPERATIONS),
        "scope": {
            "public_synthetic_only": True,
            "private_content": False,
            "live_or_external_state": False,
            "bert": False,
            "promotion": False,
            "automation": False,
        },
    }
    return {"approval": body, "sha256": hashlib.sha256(_canonical(body)).hexdigest()}


def validate_execution_approval(
    document: Mapping[str, object],
    *,
    plan_sha256: str,
    selection_sha256: str,
    implementation_commit: str,
    executor_sha256: str,
    module_sha256: str,
    overlay_manifest_sha256: str,
) -> str:
    body = document.get("approval")
    claimed = document.get("sha256")
    if not isinstance(body, Mapping) or not _is_hex(claimed) or claimed != hashlib.sha256(_canonical(body)).hexdigest():
        raise ClosedRuntimeError("closed_runtime_approval_digest_mismatch")
    expected = build_execution_approval(
        plan_sha256=plan_sha256,
        selection_sha256=selection_sha256,
        implementation_commit=implementation_commit,
        executor_sha256=executor_sha256,
        module_sha256=module_sha256,
        overlay_manifest_sha256=overlay_manifest_sha256,
    )
    if document != expected:
        raise ClosedRuntimeError("closed_runtime_approval_scope_mismatch")
    return str(claimed)


def _has_private_payload(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(str(key).lower() in _PRIVATE_KEYS or _has_private_payload(child) for key, child in value.items())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_has_private_payload(child) for child in value)
    if isinstance(value, str):
        lowered = value.lower()
        return lowered.startswith("/users/") or lowered.startswith("/home/") or "\\users\\" in lowered
    return False


def validate_execution_receipt(receipt: Mapping[str, object]) -> bool:
    if _has_private_payload(receipt):
        raise ClosedRuntimeError("closed_runtime_receipt_privacy_invalid")
    if (
        receipt.get("schema_id") != "ik.hermes.credential-bound-closed-runtime-receipt.v1"
        or receipt.get("status") != "CLEAR_CLOSED_RUNTIME_ONLY"
        or receipt.get("live_effects") is not False
    ):
        raise ClosedRuntimeError("closed_runtime_receipt_gate_failed")
    bindings = receipt.get("bindings")
    credentials = receipt.get("credential_handles")
    network = receipt.get("network")
    evaluation = receipt.get("model_evaluation")
    cell = receipt.get("ernie_cell")
    rollback = receipt.get("rollback")
    if (
        not isinstance(bindings, Mapping)
        or not all(_is_hex(bindings.get(name)) for name in ("plan_sha256", "selection_sha256"))
        or credentials != {"resolved": 2, "leak_count": 0}
        or network != {"model_worker": "CLEAR", "ernie_cell": "CLEAR", "external_access": False}
        or evaluation != {"passed": 12, "total": 12, "concurrency_passed": 2, "concurrency_total": 2}
        or cell != {"startups": 2, "restarts": 1, "health_checks": 6}
        or rollback != {"rp2": "CLEAR", "rp3_crash_recovery": "CLEAR", "rp3_pretraffic": "CLEAR"}
    ):
        raise ClosedRuntimeError("closed_runtime_receipt_gate_failed")
    return True
