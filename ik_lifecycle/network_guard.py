"""Fail-closed network-denial adapters and macOS proof receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from typing import Any, Mapping

from .models import LifecycleBlockedError


MACOS_DENY_NETWORK_POLICY = "(version 1)\n(allow default)\n(deny network*)\n"
NETWORK_PROOF_SCHEMA = "ik.hermes.macos-network-proof.v1"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise LifecycleBlockedError("network_isolation_unavailable", "network isolation runtime is unavailable") from exc


def _adapter_digest() -> str:
    return _file_digest(Path(__file__))


def bind_network_proof(source: Mapping[str, Any]) -> dict[str, Any]:
    document = json.loads(json.dumps(source))
    document.pop("proof_sha256", None)
    document["proof_sha256"] = hashlib.sha256(_canonical(document)).hexdigest()
    return document


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise LifecycleBlockedError("network_proof_invalid", "network proof timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LifecycleBlockedError("network_proof_invalid", "network proof timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise LifecycleBlockedError("network_proof_invalid", "network proof timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_network_proof(
    proof_path: Path,
    *,
    runtime: Path,
    now: datetime | None = None,
    sandbox_exec: Path = Path("/usr/bin/sandbox-exec"),
) -> dict[str, Any]:
    """Validate a current, self-bound proof against the executing adapter."""

    path = Path(proof_path)
    if not path.is_file():
        raise LifecycleBlockedError("network_proof_missing", "network-isolation proof is required")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleBlockedError("network_proof_invalid", "network-isolation proof is unreadable") from exc
    if document.get("schema_id") != NETWORK_PROOF_SCHEMA or document.get("status") != "CLEAR":
        raise LifecycleBlockedError("network_proof_invalid", "network-isolation proof is not CLEAR")
    claimed = document.get("proof_sha256")
    unsigned = {key: value for key, value in document.items() if key != "proof_sha256"}
    if claimed != hashlib.sha256(_canonical(unsigned)).hexdigest():
        raise LifecycleBlockedError("network_proof_digest_invalid", "network-isolation proof digest is invalid")
    observed = _parse_time(document.get("observed_at"))
    expires = _parse_time(document.get("expires_at"))
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current < observed or current > expires:
        raise LifecycleBlockedError("network_proof_stale", "network-isolation proof is outside its validity window")
    bindings = document.get("bindings")
    if not isinstance(bindings, Mapping):
        raise LifecycleBlockedError("network_proof_invalid", "network-isolation proof bindings are missing")
    expected = {
        "runtime": _file_digest(Path(runtime)),
        "policy": hashlib.sha256(MACOS_DENY_NETWORK_POLICY.encode("utf-8")).hexdigest(),
        "adapter": _adapter_digest(),
        "sandbox_exec": _file_digest(Path(sandbox_exec)),
    }
    for name, digest in expected.items():
        if bindings.get(f"{name}_sha256") != digest:
            raise LifecycleBlockedError(f"network_proof_{name}_drift", f"network proof {name} binding changed")
    probe = document.get("probe", {})
    if probe.get("control_connected") is not True or probe.get("sandbox_blocked") is not True:
        raise LifecycleBlockedError("network_proof_invalid", "network-isolation probe did not prove denial")
    return document


@dataclass(frozen=True)
class IsolatedCommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class MacOSNetworkIsolation:
    """Narrow per-process macOS sandbox with independently replayable proof."""

    runtime: Path
    sandbox_exec: Path = Path("/usr/bin/sandbox-exec")
    adapter_id: str = "macos-sandbox-exec-deny-network-v1"

    def _require_available(self) -> None:
        if sys.platform != "darwin" or not self.sandbox_exec.is_file() or not os.access(self.sandbox_exec, os.X_OK):
            raise LifecycleBlockedError(
                "network_isolation_unavailable",
                "a trustworthy nonprivileged macOS per-process sandbox is unavailable",
            )
        if not self.runtime.is_file() or not os.access(self.runtime, os.X_OK):
            raise LifecycleBlockedError("network_isolation_unavailable", "the bound probe runtime is unavailable")

    def create_proof(
        self,
        proof_path: Path,
        *,
        observed_at: datetime | None = None,
        ttl_seconds: int = 300,
    ) -> dict[str, Any]:
        self._require_available()
        if ttl_seconds < 1 or ttl_seconds > 900:
            raise LifecycleBlockedError("network_proof_invalid", "network proof TTL must be between 1 and 900 seconds")
        observed = (observed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.settimeout(2)
        listener.bind(("127.0.0.1", 0))
        listener.listen(2)
        host, port = listener.getsockname()
        control_connected = False
        try:
            control = socket.create_connection((host, port), timeout=2)
            accepted, _ = listener.accept()
            control_connected = True
            control.close()
            accepted.close()
            probe_code = (
                "import json,socket\n"
                f"target=({host!r},{port})\n"
                "try:\n"
                " s=socket.create_connection(target,timeout=2);s.close();print(json.dumps({'blocked':False,'errno':None}))\n"
                "except OSError as exc:\n"
                " print(json.dumps({'blocked':True,'errno':exc.errno,'kind':type(exc).__name__}))\n"
            )
            completed = subprocess.run(
                (str(self.sandbox_exec), "-p", MACOS_DENY_NETWORK_POLICY, str(self.runtime), "-c", probe_code),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "TZ": "UTC"},
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LifecycleBlockedError("network_proof_failed", "network-isolation proof could not complete") from exc
        finally:
            listener.close()
        try:
            probe = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as exc:
            raise LifecycleBlockedError("network_proof_failed", "network-isolation probe returned invalid evidence") from exc
        blocked = completed.returncode == 0 and probe.get("blocked") is True and probe.get("errno") in (1, 13)
        if not control_connected or not blocked or completed.stderr:
            raise LifecycleBlockedError("network_proof_failed", "macOS sandbox did not prove fail-closed network denial")
        receipt = bind_network_proof(
            {
                "schema_id": NETWORK_PROOF_SCHEMA,
                "status": "CLEAR",
                "adapter_id": self.adapter_id,
                "observed_at": observed.isoformat(),
                "expires_at": (observed + timedelta(seconds=ttl_seconds)).isoformat(),
                "ttl_seconds": ttl_seconds,
                "bindings": {
                    "adapter_sha256": _adapter_digest(),
                    "policy_sha256": hashlib.sha256(MACOS_DENY_NETWORK_POLICY.encode("utf-8")).hexdigest(),
                    "runtime_sha256": _file_digest(self.runtime),
                    "sandbox_exec_sha256": _file_digest(self.sandbox_exec),
                },
                "probe": {
                    "kind": "loopback_control_and_sandbox_connect",
                    "control_connected": control_connected,
                    "sandbox_blocked": blocked,
                    "sandbox_errno": probe.get("errno"),
                    "sandbox_returncode": completed.returncode,
                },
            }
        )
        output = Path(proof_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        temporary.chmod(0o400)
        os.replace(temporary, output)
        return receipt

    def run(
        self,
        argv: tuple[str, ...],
        *,
        proof_path: Path,
        now: datetime | None = None,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 60,
    ) -> IsolatedCommandResult:
        self._require_available()
        validate_network_proof(
            proof_path,
            runtime=self.runtime,
            now=now,
            sandbox_exec=self.sandbox_exec,
        )
        if not argv or not Path(argv[0]).is_absolute():
            raise LifecycleBlockedError("network_command_invalid", "isolated commands require an absolute executable")
        wrapped = (str(self.sandbox_exec), "-p", MACOS_DENY_NETWORK_POLICY, *argv)
        completed = subprocess.run(
            wrapped,
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env is not None else None,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return IsolatedCommandResult(wrapped, completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True)
class DeniedNetworkAdapter:
    enforced: bool = False
    adapter_id: str = "synthetic-fixture"

    def execute(self, argv: tuple[str, ...]) -> int:
        if not self.enforced:
            raise LifecycleBlockedError("network_denial_unproven", "network denial is not enforced")
        if not argv or argv[0] != "synthetic":
            raise LifecycleBlockedError("network_command_not_fixture", "network adapter refuses non-fixture command")
        return 0


@dataclass(frozen=True)
class NetworkDeniedReceipt:
    status: str
    adapter_id: str
    argv: tuple[str, ...]


def run_network_denied(argv: tuple[str, ...], adapter: DeniedNetworkAdapter) -> NetworkDeniedReceipt:
    if not adapter.enforced:
        raise LifecycleBlockedError("network_denial_unproven", "network denial enforcement proof is required")
    if adapter.execute(argv) != 0:
        raise LifecycleBlockedError("network_denied_command_failed", "network-denied command failed")
    return NetworkDeniedReceipt("CLEAR", adapter.adapter_id, argv)
