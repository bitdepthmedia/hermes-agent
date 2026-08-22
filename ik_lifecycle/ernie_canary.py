"""Ernie-first isolated runtime canary and rollback rehearsal.

Only aggregate, redacted evidence may leave this module.  Runtime state is
derived from the already validated migration clone, excludes every execution
or authority-bearing surface, and is discarded during RP2.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Mapping
from urllib.request import ProxyHandler, build_opener

from .composed_source import tree_digest as release_tree_digest
from .opaque_backup import (
    MacOSStorageAttestor,
    _backup_sqlite_opaquely,
    _clone_permissions_clear,
    _copy_regular_opaquely,
    _secure_mkdir,
    _sha256_file,
    _source_entries,
    _tree_digest,
)
from .promotion import PairedPointers, PromotionReceipt
from .rollback import RollbackMode, rollback_pair
from .semantic_rehearsal import _sensitive_path


_SCHEMA = "ik.ernie-runtime-canary.v1"
_PROOF_SCHEMA = "ik.hermes.macos-loopback-proof.v1"
_HEX64 = frozenset("0123456789abcdef")
_SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
MACOS_LOOPBACK_ONLY_POLICY = """(version 1)
(allow default)
(deny network*)
(allow network-bind (local ip))
(allow network-inbound (local ip))
(allow network-outbound (remote ip \"localhost:*\"))
"""
_CONTROL_COMPONENTS = frozenset(
    {
        "cron",
        "hooks",
        "plugins",
        "skills",
        "mcp",
        "node",
        "bin",
        "scripts",
        "logs",
        "cache",
        "tmp",
        "temp",
    }
)
_CONFIG_NAMES = frozenset({"config.yaml", "config.yml", "mcp.json", "settings.json", "active_profile"})
_EXECUTABLE_SUFFIXES = frozenset({".py", ".pyc", ".pyo", ".sh", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".dylib", ".so"})
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "canary_id",
        "created_at",
        "status",
        "bundle_id",
        "official_target_tag",
        "official_target_sha",
        "candidate_manifest_sha256",
        "candidate_source_tree_sha256",
        "candidate_python_sha256",
        "semantic_receipt_sha256",
        "architecture_contract_sha256",
        "source_migrated_tree_sha256",
        "runtime_profile_input_tree_sha256",
        "network_proof_sha256",
        "network_policy_sha256",
        "aggregate_counts",
        "excluded_surface_counts",
        "health_counts",
        "rollback_gates",
        "promotion_eligible",
        "retained_blockers",
        "skipped_surfaces",
    }
)


class CanaryError(RuntimeError):
    """A deliberately redacted canary failure."""


def _valid_digest(value: str) -> bool:
    return len(value) == 64 and set(value) <= _HEX64


def _safe_token(value: str, label: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if not value or len(value) > 96 or any(character not in allowed for character in value):
        raise CanaryError(f"{label}_invalid")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _file_digest(path: Path, code: str) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as error:
        raise CanaryError(code) from error


def _adapter_digest() -> str:
    return _file_digest(Path(__file__), "network_isolation_unavailable")


def _private_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


@dataclass(frozen=True)
class LoopbackProof:
    proof_sha256: str
    policy_sha256: str
    adapter_sha256: str
    runtime_sha256: str
    sandbox_exec_sha256: str
    observed_at: datetime
    expires_at: datetime
    proof_path: Path | None = None


class LoopbackOnlyMacOSSandbox:
    """Per-process macOS boundary: loopback is allowed; all else is denied."""

    def __init__(self, runtime: Path, sandbox_exec: Path = _SANDBOX_EXEC) -> None:
        self.runtime = Path(runtime).resolve()
        self.sandbox_exec = Path(sandbox_exec)

    def _require(self) -> None:
        if sys.platform != "darwin" or not self.sandbox_exec.is_file() or not os.access(self.sandbox_exec, os.X_OK):
            raise CanaryError("network_isolation_unavailable")
        if not self.runtime.is_file() or not os.access(self.runtime, os.X_OK):
            raise CanaryError("network_runtime_unavailable")

    def create_proof(self, proof_path: Path, *, ttl_seconds: int = 300) -> LoopbackProof:
        self._require()
        if not 1 <= ttl_seconds <= 300:
            raise CanaryError("network_proof_ttl_invalid")
        program = (
            "import json,socket,sys\n"
            "listener=socket.socket();listener.settimeout(2);listener.bind(('127.0.0.1',0));listener.listen(1)\n"
            "port=listener.getsockname()[1]\n"
            "client=socket.create_connection(('127.0.0.1',port),timeout=2);accepted,_=listener.accept()\n"
            "client.close();accepted.close();listener.close()\n"
            "blocked=False;err=None\n"
            "try:\n socket.create_connection(('1.1.1.1',53),timeout=1);sys.exit(7)\n"
            "except OSError as exc:\n blocked=exc.errno in (1,13);err=exc.errno\n"
            "print(json.dumps({'loopback':True,'external_blocked':blocked,'errno':err},sort_keys=True))\n"
            "sys.exit(0 if blocked else 8)\n"
        )
        try:
            completed = subprocess.run(
                (str(self.sandbox_exec), "-p", MACOS_LOOPBACK_ONLY_POLICY, str(self.runtime), "-B", "-c", program),
                env={"PATH": "/usr/bin:/bin", "LANG": "C", "TZ": "UTC", "PYTHONDONTWRITEBYTECODE": "1"},
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CanaryError("network_proof_failed") from error
        try:
            probe = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as error:
            raise CanaryError("network_proof_failed") from error
        sandbox_errno = probe.get("errno")
        if (
            completed.returncode != 0
            or completed.stderr
            or probe.get("loopback") is not True
            or probe.get("external_blocked") is not True
            or sandbox_errno not in (1, 13)
        ):
            raise CanaryError("network_proof_failed")
        observed = datetime.now(timezone.utc)
        unsigned: dict[str, object] = {
            "schema_id": _PROOF_SCHEMA,
            "status": "CLEAR",
            "observed_at": observed.isoformat(),
            "expires_at": (observed + timedelta(seconds=ttl_seconds)).isoformat(),
            "ttl_seconds": ttl_seconds,
            "bindings": {
                "policy_sha256": hashlib.sha256(MACOS_LOOPBACK_ONLY_POLICY.encode()).hexdigest(),
                "adapter_sha256": _adapter_digest(),
                "runtime_sha256": _file_digest(self.runtime, "network_runtime_unavailable"),
                "sandbox_exec_sha256": _file_digest(self.sandbox_exec, "network_isolation_unavailable"),
            },
            "probe": {"loopback_roundtrip": True, "external_blocked": True, "sandbox_errno": sandbox_errno},
        }
        document = dict(unsigned)
        document["proof_sha256"] = hashlib.sha256(_canonical(unsigned)).hexdigest()
        output = Path(proof_path)
        _private_json(output, document)
        os.chmod(output, 0o400)
        return self.validate(output)

    def validate(self, proof_path: Path, *, now: datetime | None = None) -> LoopbackProof:
        self._require()
        try:
            document = json.loads(Path(proof_path).read_text(encoding="utf-8"))
            observed = datetime.fromisoformat(str(document["observed_at"]).replace("Z", "+00:00"))
            expires = datetime.fromisoformat(str(document["expires_at"]).replace("Z", "+00:00"))
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
            raise CanaryError("network_proof_invalid") from error
        if document.get("schema_id") != _PROOF_SCHEMA or document.get("status") != "CLEAR":
            raise CanaryError("network_proof_invalid")
        claimed = document.get("proof_sha256")
        unsigned = {key: value for key, value in document.items() if key != "proof_sha256"}
        if claimed != hashlib.sha256(_canonical(unsigned)).hexdigest():
            raise CanaryError("network_proof_digest_invalid")
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if observed.tzinfo is None or expires.tzinfo is None or not observed <= current <= expires or (expires - observed).total_seconds() > 300:
            raise CanaryError("network_proof_stale")
        bindings = document.get("bindings")
        expected = {
            "policy_sha256": hashlib.sha256(MACOS_LOOPBACK_ONLY_POLICY.encode()).hexdigest(),
            "adapter_sha256": _adapter_digest(),
            "runtime_sha256": _file_digest(self.runtime, "network_runtime_unavailable"),
            "sandbox_exec_sha256": _file_digest(self.sandbox_exec, "network_isolation_unavailable"),
        }
        if not isinstance(bindings, dict) or any(bindings.get(key) != value for key, value in expected.items()):
            raise CanaryError("network_proof_binding_drift")
        probe = document.get("probe")
        if (
            not isinstance(probe, dict)
            or probe.get("loopback_roundtrip") is not True
            or probe.get("external_blocked") is not True
            or probe.get("sandbox_errno") not in (1, 13)
        ):
            raise CanaryError("network_proof_invalid")
        return LoopbackProof(
            proof_sha256=str(claimed),
            policy_sha256=expected["policy_sha256"],
            adapter_sha256=expected["adapter_sha256"],
            runtime_sha256=expected["runtime_sha256"],
            sandbox_exec_sha256=expected["sandbox_exec_sha256"],
            observed_at=observed.astimezone(timezone.utc),
            expires_at=expires.astimezone(timezone.utc),
            proof_path=Path(proof_path),
        )

    def wrap(self, argv: tuple[str, ...], proof: LoopbackProof) -> tuple[str, ...]:
        if proof.proof_path is None:
            raise CanaryError("network_proof_missing")
        validated = self.validate(proof.proof_path)
        if validated.proof_sha256 != proof.proof_sha256:
            raise CanaryError("network_proof_binding_drift")
        if not argv or not Path(argv[0]).is_absolute():
            raise CanaryError("runtime_command_invalid")
        try:
            executable = Path(argv[0]).resolve(strict=True)
        except OSError as error:
            raise CanaryError("network_runtime_drift") from error
        if executable != self.runtime or _file_digest(executable, "network_runtime_unavailable") != proof.runtime_sha256:
            raise CanaryError("network_runtime_drift")
        return (str(self.sandbox_exec), "-p", MACOS_LOOPBACK_ONLY_POLICY, *argv)


@dataclass(frozen=True)
class CanaryRequest:
    storage_root: Path
    migrated_profile_root: Path
    expected_migrated_tree_sha256: str
    semantic_receipt_sha256: str
    architecture_contract_sha256: str
    candidate_release_root: Path
    candidate_source_root: Path
    candidate_manifest_path: Path
    expected_candidate_manifest_sha256: str
    candidate_python: Path
    expected_candidate_python_sha256: str
    bundle_id: str
    official_target_tag: str
    official_target_sha: str
    canary_id: str
    denied_roots: tuple[Path, ...]
    rollback_artifact_path: Path | None = None
    expected_rollback_artifact_sha256: str | None = None


@dataclass(frozen=True)
class CanaryReceipt:
    schema_version: str
    canary_id: str
    created_at: str
    status: str
    bundle_id: str
    official_target_tag: str
    official_target_sha: str
    candidate_manifest_sha256: str
    candidate_source_tree_sha256: str
    candidate_python_sha256: str
    semantic_receipt_sha256: str
    architecture_contract_sha256: str
    source_migrated_tree_sha256: str
    runtime_profile_input_tree_sha256: str
    network_proof_sha256: str
    network_policy_sha256: str
    aggregate_counts: dict[str, int]
    excluded_surface_counts: dict[str, int]
    health_counts: dict[str, int]
    rollback_gates: dict[str, str]
    promotion_eligible: bool
    retained_blockers: tuple[str, ...]
    skipped_surfaces: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["retained_blockers"] = list(self.retained_blockers)
        payload["skipped_surfaces"] = list(self.skipped_surfaces)
        return payload


@dataclass(frozen=True)
class CanaryResult:
    receipt: CanaryReceipt
    receipt_path: Path
    runtime_profile_root: Path


@dataclass
class ProcessHandle:
    process: subprocess.Popen[bytes]
    port: int
    version: str
    stdout_stream: Any
    stderr_stream: Any


class CanaryRuntime:
    def start(self, request: CanaryRequest, *, profile_root: Path, run_root: Path, proof: LoopbackProof):
        raise NotImplementedError

    def health(self, handle) -> dict[str, object]:
        raise NotImplementedError

    def heartbeat(self, handle) -> dict[str, object]:
        raise NotImplementedError

    def stop(self, handle) -> None:
        raise NotImplementedError


class ProcessCanaryRuntime(CanaryRuntime):
    def __init__(self, sandbox: LoopbackOnlyMacOSSandbox, *, startup_timeout_seconds: int = 90) -> None:
        self.sandbox = sandbox
        self.startup_timeout_seconds = startup_timeout_seconds

    @staticmethod
    def _environment(request: CanaryRequest, profile_root: Path, run_root: Path, ready_file: Path) -> dict[str, str]:
        home = run_root / "home"
        _secure_mkdir(home)
        return {
            "CI": "1",
            "HOME": os.fspath(home),
            "HERMES_HOME": os.fspath(profile_root),
            "HERMES_DESKTOP_READY_FILE": os.fspath(ready_file),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "NO_PROXY": "127.0.0.1,localhost",
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONPATH": os.fspath(request.candidate_source_root),
            "TZ": "UTC",
        }

    def start(self, request: CanaryRequest, *, profile_root: Path, run_root: Path, proof: LoopbackProof) -> ProcessHandle:
        ready = run_root / f"ready-{time.monotonic_ns()}.json"
        stdout_path = run_root / f"runtime-{time.monotonic_ns()}.stdout.log"
        stderr_path = run_root / f"runtime-{time.monotonic_ns()}.stderr.log"
        stdout_stream = stdout_path.open("xb", buffering=0)
        stderr_stream = stderr_path.open("xb", buffering=0)
        os.chmod(stdout_path, 0o600)
        os.chmod(stderr_path, 0o600)
        argv = (
            os.fspath(request.candidate_python),
            "-B",
            "-m",
            "hermes_cli.main",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--isolated",
            "--skip-build",
        )
        wrapped = self.sandbox.wrap(argv, proof)
        try:
            process = subprocess.Popen(
                wrapped,
                cwd=request.candidate_source_root,
                env=self._environment(request, profile_root, run_root, ready),
                stdin=subprocess.DEVNULL,
                stdout=stdout_stream,
                stderr=stderr_stream,
                start_new_session=True,
            )
        except OSError as error:
            stdout_stream.close()
            stderr_stream.close()
            raise CanaryError("runtime_start_failed") from error
        deadline = time.monotonic() + self.startup_timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                self._terminate_process(process)
                stdout_stream.close()
                stderr_stream.close()
                raise CanaryError("runtime_start_failed")
            if ready.is_file():
                try:
                    document = json.loads(ready.read_text(encoding="utf-8"))
                    port = int(document["port"])
                except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                    self._terminate_process(process)
                    stdout_stream.close()
                    stderr_stream.close()
                    raise CanaryError("runtime_ready_invalid") from error
                if not 1024 <= port <= 65535:
                    self._terminate_process(process)
                    stdout_stream.close()
                    stderr_stream.close()
                    raise CanaryError("runtime_port_invalid")
                handle = ProcessHandle(process, port, "", stdout_stream, stderr_stream)
                try:
                    health = self.health(handle)
                    handle.version = str(health.get("version", ""))
                    return handle
                except CanaryError:
                    self._terminate_process(process)
                    stdout_stream.close()
                    stderr_stream.close()
                    raise
            time.sleep(0.1)
        self._terminate_process(process)
        stdout_stream.close()
        stderr_stream.close()
        raise CanaryError("runtime_start_timeout")

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError as error:
            raise CanaryError("runtime_stop_failed") from error
        if process.poll() is None:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
        # The parent may have exited before startup completed while a child
        # inherited its process group.  Terminate that group even when the
        # direct child is already reaped, then force only lingering members.
        time.sleep(0.1)
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        except OSError as error:
            raise CanaryError("runtime_stop_failed") from error
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError as error:
            raise CanaryError("runtime_stop_failed") from error
        if process.poll() is None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired as error:
                raise CanaryError("runtime_stop_failed") from error

    def health(self, handle: ProcessHandle) -> dict[str, object]:
        opener = build_opener(ProxyHandler({}))
        try:
            with opener.open(f"http://127.0.0.1:{handle.port}/api/health", timeout=5) as response:
                payload = json.loads(response.read(4096))
        except Exception as error:
            raise CanaryError("runtime_health_failed") from error
        if response.status != 200 or payload.get("ok") is not True or payload.get("auth_required") is not False:
            raise CanaryError("runtime_health_failed")
        return payload

    def heartbeat(self, handle: ProcessHandle) -> dict[str, object]:
        time.sleep(0.5)
        if handle.process.poll() is not None:
            raise CanaryError("runtime_heartbeat_failed")
        return self.health(handle)

    def stop(self, handle: ProcessHandle) -> None:
        self._terminate_process(handle.process)
        handle.stdout_stream.close()
        handle.stderr_stream.close()
        if handle.process.returncode not in (0, -signal.SIGTERM):
            raise CanaryError("runtime_stop_failed")


def _classify_exclusion(relative: PurePosixPath) -> str | None:
    lowered = tuple(part.lower() for part in relative.parts)
    if _sensitive_path(relative):
        return "credentials"
    if any(component in _CONTROL_COMPONENTS for component in lowered[:-1]):
        if "cron" in lowered:
            return "schedules"
        return "execution_surfaces"
    name = lowered[-1] if lowered else ""
    if name in _CONFIG_NAMES:
        return "configuration"
    if PurePosixPath(name).suffix.lower() in _EXECUTABLE_SUFFIXES:
        return "execution_surfaces"
    return None


def _build_runtime_profile(source: Path, destination: Path) -> tuple[str, dict[str, int], dict[str, int]]:
    _secure_mkdir(destination)
    copied_files = 0
    copied_bytes = 0
    excluded = {"credentials": 0, "configuration": 0, "schedules": 0, "execution_surfaces": 0}
    for path, relative_text, is_directory in _source_entries(source):
        relative = PurePosixPath(relative_text)
        classification = _classify_exclusion(relative)
        if classification is not None:
            if not is_directory:
                excluded[classification] += 1
            continue
        target = destination.joinpath(*relative.parts)
        if is_directory:
            _secure_mkdir(target)
            continue
        _secure_mkdir(target.parent)
        try:
            with path.open("rb") as stream:
                sqlite_file = stream.read(16) == b"SQLite format 3\x00"
        except OSError as error:
            raise CanaryError("runtime_profile_copy_failed") from error
        try:
            if sqlite_file:
                _backup_sqlite_opaquely(path, target)
            else:
                _copy_regular_opaquely(path, target, None)
        except Exception as error:
            raise CanaryError("runtime_profile_copy_failed") from error
        copied_files += 1
        copied_bytes += target.stat().st_size
    for path in sorted(destination.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    os.chmod(destination, 0o700)
    if not _clone_permissions_clear(destination):
        raise CanaryError("runtime_profile_permissions_invalid")
    tree_sha256, file_count, total_bytes = _tree_digest(destination)
    if (file_count, total_bytes) != (copied_files, copied_bytes):
        raise CanaryError("runtime_profile_copy_failed")
    return tree_sha256, {"runtime_files": file_count, "runtime_bytes": total_bytes}, excluded


def discard_runtime_profile(profile_root: Path, run_root: Path) -> None:
    """RP2 discard of one exact disposable tree; never follows symlinks."""

    root = Path(os.path.abspath(os.fspath(run_root)))
    profile = Path(os.path.abspath(os.fspath(profile_root)))
    if (
        not root.is_dir()
        or root.is_symlink()
        or profile.parent != root
        or profile.name != "runtime-profile"
        or not profile.is_dir()
        or profile.is_symlink()
    ):
        raise CanaryError("rp2_target_invalid")
    try:
        entries = list(os.walk(profile, topdown=False, followlinks=False))
        for directory, directory_names, file_names in entries:
            directory_path = Path(directory)
            for name in (*directory_names, *file_names):
                child = directory_path / name
                metadata = os.lstat(child)
                if stat.S_ISLNK(metadata.st_mode):
                    raise CanaryError("rp2_symlink_rejected")
                if metadata.st_uid != os.getuid() or not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                    raise CanaryError("rp2_ownership_or_type_invalid")
                os.chmod(child, 0o700 if stat.S_ISDIR(metadata.st_mode) else 0o600)
            os.chmod(directory_path, 0o700)
        shutil.rmtree(profile)
    except CanaryError:
        raise
    except OSError as error:
        raise CanaryError("rp2_discard_failed") from error
    if profile.exists():
        raise CanaryError("rp2_discard_failed")


def receipt_is_redacted(payload: Mapping[str, object]) -> bool:
    if set(payload) != _RECEIPT_FIELDS:
        return False
    rendered = json.dumps(payload, sort_keys=True)
    if any(marker in rendered for marker in ("/Users/", "/Volumes/", "private-session", "SYNTHETIC_PRIVATE")):
        return False
    if any(key.endswith("path") or key.endswith("root") for key in payload):
        return False
    return True


class ErnieCanaryEngine:
    def __init__(
        self,
        *,
        attestor: MacOSStorageAttestor | None = None,
        sandbox: Any | None = None,
        runtime: CanaryRuntime | None = None,
    ) -> None:
        self._attestor = attestor or MacOSStorageAttestor()
        self._sandbox = sandbox
        self._runtime = runtime

    def execute(self, request: CanaryRequest, *, verify_source_digest: bool = True) -> CanaryResult:
        canary_id = _safe_token(request.canary_id, "canary_id")
        bundle_id = _safe_token(request.bundle_id, "bundle_id")
        digests = (
            request.expected_migrated_tree_sha256,
            request.semantic_receipt_sha256,
            request.architecture_contract_sha256,
            request.expected_candidate_manifest_sha256,
            request.expected_candidate_python_sha256,
        )
        if not all(_valid_digest(value) for value in digests):
            raise CanaryError("canary_binding_invalid")
        storage = Path(request.storage_root).absolute()
        source = Path(request.migrated_profile_root).absolute()
        release = Path(request.candidate_release_root).absolute()
        candidate_source = Path(request.candidate_source_root).absolute()
        manifest = Path(request.candidate_manifest_path).absolute()
        runtime_path = Path(request.candidate_python).resolve()
        if not self._attestor.attest(storage, denied_roots=request.denied_roots).clear:
            raise CanaryError("storage_policy_blocked")
        if not source.is_dir() or source.is_symlink() or not candidate_source.is_dir() or candidate_source.is_symlink():
            raise CanaryError("canary_target_invalid")
        if (
            not release.is_dir()
            or release.is_symlink()
            or not manifest.is_file()
            or manifest.is_symlink()
            or _file_digest(manifest, "candidate_binding_invalid") != request.expected_candidate_manifest_sha256
            or not runtime_path.is_file()
            or _file_digest(runtime_path, "candidate_runtime_invalid") != request.expected_candidate_python_sha256
        ):
            raise CanaryError("candidate_binding_invalid")
        try:
            manifest_document = json.loads(manifest.read_text(encoding="utf-8"))
            identity = manifest_document["identity"]
            expected_source_digest = identity["bindings"]["composed-source"]["tree_sha256"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise CanaryError("candidate_binding_invalid") from error
        if (
            manifest_document.get("status") != "SEALED_CODE_ONLY"
            or manifest_document.get("bundle_id") != bundle_id
            or identity.get("target_tag") != request.official_target_tag
            or identity.get("target_commit_sha") != request.official_target_sha
        ):
            raise CanaryError("candidate_identity_invalid")
        observed_source_digest = release_tree_digest(candidate_source)
        if observed_source_digest != expected_source_digest:
            raise CanaryError("candidate_source_drift")
        source_digest_before, _, _ = _tree_digest(source)
        if verify_source_digest and source_digest_before != request.expected_migrated_tree_sha256:
            raise CanaryError("semantic_clone_drift")
        rollback_digest_before = None
        if request.rollback_artifact_path is not None:
            if not request.expected_rollback_artifact_sha256 or not _valid_digest(request.expected_rollback_artifact_sha256):
                raise CanaryError("rollback_binding_invalid")
            rollback_digest_before = _file_digest(request.rollback_artifact_path, "rollback_artifact_missing")
            if rollback_digest_before != request.expected_rollback_artifact_sha256:
                raise CanaryError("rollback_artifact_drift")

        canary_root = storage / "canaries" / canary_id
        _secure_mkdir(storage / "canaries")
        if canary_root.exists():
            raise CanaryError("canary_id_already_used")
        _secure_mkdir(canary_root)
        runtime_profile = canary_root / "runtime-profile"
        proof_path = canary_root / "network-proof.json"
        receipt_path = storage / "canary-receipts" / f"{canary_id}.json"
        try:
            profile_digest, aggregate_counts, excluded_counts = _build_runtime_profile(source, runtime_profile)
            source_digest_after_copy, _, _ = _tree_digest(source)
            if source_digest_after_copy != source_digest_before:
                raise CanaryError("semantic_clone_concurrent_mutation")
            sandbox = self._sandbox or LoopbackOnlyMacOSSandbox(runtime_path)
            proof = sandbox.create_proof(proof_path, ttl_seconds=300)
            runtime = self._runtime or ProcessCanaryRuntime(sandbox)
            health_versions: list[str] = []
            for _ in range(2):
                handle = runtime.start(request, profile_root=runtime_profile, run_root=canary_root, proof=proof)
                try:
                    health = runtime.health(handle)
                    if health.get("ok") is not True or health.get("auth_required") is not False:
                        raise CanaryError("runtime_health_failed")
                    health_versions.append(str(health.get("version", "")))
                    heartbeat = runtime.heartbeat(handle)
                    if heartbeat.get("ok") is not True or heartbeat.get("version") != health.get("version"):
                        raise CanaryError("runtime_heartbeat_failed")
                finally:
                    runtime.stop(handle)
            if len(set(health_versions)) != 1 or not health_versions[0]:
                raise CanaryError("runtime_version_drift")

            pointer_root = canary_root / "pointers"
            pointers = PairedPointers(pointer_root / "release.json", pointer_root / "profile.json", pointer_root / "journal.json")
            pointers.initialize("legacy-release", "legacy-profile", 1)
            try:
                pointers.switch(bundle_id, "canary-profile", 2, crash_after_release=True)
            except RuntimeError:
                pointers.recover()
            if pointers.read_pair() != ("legacy-release", "legacy-profile", 1):
                raise CanaryError("rp3_crash_recovery_failed")
            pointers.switch(bundle_id, "canary-profile", 2)
            promotion = PromotionReceipt("legacy-release", "legacy-profile", 1, bundle_id, "canary-profile", 2)
            rollback = rollback_pair(pointers, promotion, RollbackMode.PRE_TRAFFIC, delta_reconciled=False)
            if rollback.status != "ROLLED_BACK" or pointers.read_pair() != ("legacy-release", "legacy-profile", 1):
                raise CanaryError("rp3_pretraffic_rollback_failed")

            discard_runtime_profile(runtime_profile, canary_root)
            source_digest_final, _, _ = _tree_digest(source)
            if source_digest_final != source_digest_before:
                raise CanaryError("semantic_clone_changed")
            candidate_source_digest_final = release_tree_digest(candidate_source)
            if candidate_source_digest_final != observed_source_digest:
                raise CanaryError("candidate_source_changed")
            if rollback_digest_before is not None and _file_digest(request.rollback_artifact_path, "rollback_artifact_missing") != rollback_digest_before:
                raise CanaryError("rollback_artifact_changed")

            receipt = CanaryReceipt(
                schema_version=_SCHEMA,
                canary_id=canary_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                status="CLEAR_SAFE_LOCAL_CANARY",
                bundle_id=bundle_id,
                official_target_tag=request.official_target_tag,
                official_target_sha=request.official_target_sha,
                candidate_manifest_sha256=request.expected_candidate_manifest_sha256,
                candidate_source_tree_sha256=observed_source_digest,
                candidate_python_sha256=request.expected_candidate_python_sha256,
                semantic_receipt_sha256=request.semantic_receipt_sha256,
                architecture_contract_sha256=request.architecture_contract_sha256,
                source_migrated_tree_sha256=source_digest_before,
                runtime_profile_input_tree_sha256=profile_digest,
                network_proof_sha256=proof.proof_sha256,
                network_policy_sha256=proof.policy_sha256,
                aggregate_counts={**aggregate_counts, "startups": 2, "restarts": 1, "health_checks": 6, "rollback_rehearsals": 2},
                excluded_surface_counts=excluded_counts,
                health_counts={"ready_sentinel_clear": 2, "endpoint_clear": 2, "heartbeat_clear": 2, "restart_clear": 1, "runtime_code_parity_clear": 1},
                rollback_gates={"rp2": "CLEAR", "rp3_crash_recovery": "CLEAR", "rp3_pretraffic": "CLEAR", "immutable_backup": "CLEAR" if rollback_digest_before else "NOT_BOUND"},
                promotion_eligible=False,
                retained_blockers=(
                    "real-model-evaluation",
                    "credential-bound-runtime",
                    "schedule-reconciliation",
                    "automation-approval",
                    "live-ernie-promotion",
                    "live-bert-separate-phase",
                ),
                skipped_surfaces=("models", "credentials", "schedules", "automations", "messaging", "live-ernie", "live-bert", "promotion", "deployment", "push"),
            )
            payload = receipt.to_dict()
            if not receipt_is_redacted(payload):
                raise CanaryError("receipt_privacy_gate_failed")
            _private_json(receipt_path, payload)
            return CanaryResult(receipt, receipt_path, runtime_profile)
        except CanaryError:
            # The isolated failure root and private logs are retained for local diagnosis.
            raise
