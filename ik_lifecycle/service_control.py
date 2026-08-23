"""Fail-closed service control used only by digest-bound cell promotion."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import time
from typing import Callable, Protocol

from .models import LifecycleBlockedError
from .promotion import ApprovalReceipt, PairedPointers, PromotionReceipt, promote_pair
from .rollback import RollbackMode, rollback_pair


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class ServicePreflight:
    running: bool
    state: str


@dataclass(frozen=True)
class ServicePromotionResult:
    status: str


class CommandRunner(Protocol):
    def __call__(self, argv: tuple[str, ...]) -> CommandResult: ...


def _run(argv: tuple[str, ...]) -> CommandResult:
    completed = subprocess.run(argv, check=False, capture_output=True, text=True, timeout=30)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class ServiceAdapter(Protocol):
    def preflight(self) -> ServicePreflight: ...
    def close(self) -> None: ...
    def closed(self) -> bool: ...
    def open(self) -> None: ...


class ServiceGroupAdapter:
    """Treat an ordered model-to-gateway service set as one promotion unit."""

    def __init__(
        self,
        services: tuple[ServiceAdapter, ...],
        *,
        quiescence_seconds: float = 0.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not services:
            raise LifecycleBlockedError("service_group_empty", "service group is empty")
        if not 0.0 <= quiescence_seconds <= 300.0:
            raise LifecycleBlockedError("service_group_quiescence_invalid", "service group quiescence is invalid")
        self.services = services
        self.quiescence_seconds = quiescence_seconds
        self.monotonic = monotonic
        self.sleeper = sleeper
        self._closed_at: float | None = None

    def preflight(self) -> ServicePreflight:
        states = [service.preflight() for service in self.services]
        running = all(state.running for state in states)
        return ServicePreflight(running, "running" if running else "partial")

    def close(self) -> None:
        for service in reversed(self.services):
            service.close()
        self._closed_at = self.monotonic()

    def closed(self) -> bool:
        return all(service.closed() for service in self.services)

    def open(self) -> None:
        if self._closed_at is not None:
            remaining = self.quiescence_seconds - (self.monotonic() - self._closed_at)
            if remaining > 0:
                self.sleeper(remaining)
        for service in self.services:
            service.open()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class PairedSymlinks:
    """Service-closed release/profile symlinks with crash recovery journal."""

    def __init__(
        self,
        release_path: Path,
        profile_path: Path,
        journal_path: Path,
        *,
        allowed_release_root: Path | None = None,
        allowed_profile_root: Path | None = None,
        allowed_release_roots: tuple[Path, ...] | None = None,
        allowed_profile_roots: tuple[Path, ...] | None = None,
    ) -> None:
        self.release_path = Path(release_path).absolute()
        self.profile_path = Path(profile_path).absolute()
        self.journal_path = Path(journal_path).absolute()
        release_roots = allowed_release_roots or (() if allowed_release_root is None else (allowed_release_root,))
        profile_roots = allowed_profile_roots or (() if allowed_profile_root is None else (allowed_profile_root,))
        if not release_roots or not profile_roots:
            raise LifecycleBlockedError("pointer_roots_missing", "cell pointer roots are not declared")
        self.allowed_release_roots = tuple(Path(root).resolve() for root in release_roots)
        self.allowed_profile_roots = tuple(Path(root).resolve() for root in profile_roots)

    @staticmethod
    def _inside(target: Path, root: Path) -> bool:
        try:
            target.relative_to(root)
            return True
        except ValueError:
            return False

    def _targets(self, release: Path, profile: Path) -> tuple[Path, Path]:
        try:
            resolved_release = Path(release).resolve(strict=True)
            resolved_profile = Path(profile).resolve(strict=True)
        except OSError as exc:
            raise LifecycleBlockedError("pointer_target_missing", "cell pointer target is unavailable") from exc
        if not resolved_release.is_dir() or not any(
            self._inside(resolved_release, root) for root in self.allowed_release_roots
        ):
            raise LifecycleBlockedError("release_target_invalid", "release pointer target is outside its cell root")
        if not resolved_profile.is_dir() or not any(
            self._inside(resolved_profile, root) for root in self.allowed_profile_roots
        ):
            raise LifecycleBlockedError("profile_target_invalid", "profile pointer target is outside its cell root")
        return resolved_release, resolved_profile

    @staticmethod
    def _switch_link(path: Path, target: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.symlink_to(target, target_is_directory=True)
        os.replace(temporary, path)

    def initialize(self, release: Path, profile: Path, generation: int) -> None:
        if self.release_path.exists() or self.release_path.is_symlink() or self.profile_path.exists() or self.profile_path.is_symlink():
            raise LifecycleBlockedError("pointer_already_initialized", "cell pointers already exist")
        release_target, profile_target = self._targets(release, profile)
        self._switch_link(self.release_path, release_target)
        self._switch_link(self.profile_path, profile_target)
        current = [str(release_target), str(profile_target), generation]
        _atomic_json(self.journal_path, {"state": "complete", "current": current})

    def read_pair(self) -> tuple[str, str, int]:
        try:
            journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
            release = self.release_path.resolve(strict=True)
            profile = self.profile_path.resolve(strict=True)
            current = journal["current"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise LifecycleBlockedError("pointer_pair_invalid", "cell pointer pair is invalid") from exc
        if journal.get("state") != "complete" or current[:2] != [str(release), str(profile)]:
            raise LifecycleBlockedError("pointer_pair_mixed", "cell pointer pair does not match its journal")
        return str(release), str(profile), int(current[2])

    def switch(
        self,
        release: Path,
        profile: Path,
        generation: int,
        *,
        service_closed: bool,
        crash_after_release: bool = False,
    ) -> None:
        if not service_closed:
            raise LifecycleBlockedError("service_not_closed", "service must be closed before pointer switch")
        previous = self.read_pair()
        release_target, profile_target = self._targets(release, profile)
        next_pair = [str(release_target), str(profile_target), generation]
        _atomic_json(self.journal_path, {"state": "switching", "previous": list(previous), "next": next_pair})
        self._switch_link(self.release_path, release_target)
        if crash_after_release:
            raise RuntimeError("injected pointer-switch crash")
        self._switch_link(self.profile_path, profile_target)
        _atomic_json(self.journal_path, {"state": "complete", "current": next_pair, "previous": list(previous)})

    def recover(self, *, service_closed: bool) -> None:
        if not service_closed:
            raise LifecycleBlockedError("service_not_closed", "service must be closed before pointer recovery")
        try:
            journal = json.loads(self.journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LifecycleBlockedError("pointer_journal_invalid", "cell pointer journal is invalid") from exc
        if journal.get("state") != "switching":
            return
        previous = journal.get("previous")
        if not isinstance(previous, list) or len(previous) != 3:
            raise LifecycleBlockedError("pointer_journal_invalid", "cell pointer journal is invalid")
        release, profile = self._targets(Path(previous[0]), Path(previous[1]))
        self._switch_link(self.release_path, release)
        self._switch_link(self.profile_path, profile)
        _atomic_json(self.journal_path, {"state": "complete", "current": [str(release), str(profile), int(previous[2])], "recovered": True})


class LaunchdServiceAdapter:
    def __init__(
        self,
        *,
        label: str,
        plist_path: Path,
        expected_program: str,
        expected_workdir: str,
        expected_profile: str | None,
        uid: int,
        runner: CommandRunner = _run,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", label) or uid < 0:
            raise LifecycleBlockedError("launchd_identity_invalid", "launchd service identity is invalid")
        self.label = label
        self.plist_path = Path(plist_path).resolve()
        self.expected_program = expected_program
        self.expected_workdir = expected_workdir
        self.expected_profile = expected_profile
        self.uid = uid
        self.runner = runner

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    def _definition(self) -> dict[str, object]:
        if self.plist_path.is_symlink() or not self.plist_path.is_file():
            raise LifecycleBlockedError("launchd_plist_invalid", "launchd service definition is missing or a symlink")
        try:
            document = plistlib.loads(self.plist_path.read_bytes())
        except Exception as exc:
            raise LifecycleBlockedError("launchd_plist_invalid", "launchd service definition is invalid") from exc
        arguments = document.get("ProgramArguments", [])
        environment = document.get("EnvironmentVariables", {})
        if document.get("Label") != self.label:
            raise LifecycleBlockedError("launchd_label_mismatch", "launchd service label mismatch")
        if not isinstance(arguments, list) or not arguments or arguments[0] != self.expected_program:
            raise LifecycleBlockedError("launchd_program_mismatch", "launchd service program mismatch")
        if document.get("WorkingDirectory") != self.expected_workdir:
            raise LifecycleBlockedError("launchd_workdir_mismatch", "launchd service working directory mismatch")
        if not isinstance(environment, dict):
            raise LifecycleBlockedError("launchd_profile_mismatch", "launchd service profile mismatch")
        if self.expected_profile is None:
            if "HERMES_HOME" in environment:
                raise LifecycleBlockedError("launchd_profile_mismatch", "launchd service profile must be verified separately")
        elif environment.get("HERMES_HOME") != self.expected_profile:
            raise LifecycleBlockedError("launchd_profile_mismatch", "launchd service profile mismatch")
        return document

    def preflight(self) -> ServicePreflight:
        self._definition()
        result = self.runner(("/bin/launchctl", "print", f"{self.domain}/{self.label}"))
        if result.returncode != 0:
            return ServicePreflight(False, "unloaded")
        running = bool(re.search(r"\bstate\s*=\s*running\b", result.stdout))
        if not running:
            raise LifecycleBlockedError("launchd_state_ambiguous", "launchd service state is not running")
        return ServicePreflight(True, "running")

    def close(self) -> None:
        self._definition()
        result = self.runner(("/bin/launchctl", "bootout", self.domain, str(self.plist_path)))
        if result.returncode != 0:
            raise LifecycleBlockedError("launchd_close_failed", "launchd service failed to close")

    def closed(self) -> bool:
        result = self.runner(("/bin/launchctl", "print", f"{self.domain}/{self.label}"))
        return result.returncode != 0

    def open(self) -> None:
        self._definition()
        result = self.runner(("/bin/launchctl", "bootstrap", self.domain, str(self.plist_path)))
        if result.returncode != 0:
            raise LifecycleBlockedError("launchd_open_failed", "launchd service failed to open")


class LaunchdDefinitionTransaction:
    """Atomically stage one reviewed launchd definition without overwriting drift."""

    def __init__(self, source: Path, destination: Path, *, expected_sha256: str | None = None) -> None:
        self.source = Path(source).absolute()
        self.destination = Path(destination).absolute()
        self.expected_sha256 = expected_sha256
        self._created = False

    @staticmethod
    def _digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def prepare(self) -> None:
        if self.source.is_symlink() or not self.source.is_file():
            raise LifecycleBlockedError("launchd_definition_invalid", "sealed launchd definition is unavailable")
        source_digest = self._digest(self.source)
        if self.expected_sha256 and source_digest != self.expected_sha256:
            raise LifecycleBlockedError("launchd_definition_digest_drift", "sealed launchd definition digest changed")
        if self.destination.is_symlink():
            raise LifecycleBlockedError("launchd_definition_invalid", "launchd definition destination is a symlink")
        if self.destination.exists():
            if not self.destination.is_file() or self._digest(self.destination) != source_digest:
                raise LifecycleBlockedError("launchd_definition_drift", "existing launchd definition differs")
            return
        self.destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.destination.parent.resolve() != self.destination.parent:
            raise LifecycleBlockedError("launchd_definition_invalid", "launchd definition parent uses a symlink")
        temporary = self.destination.with_name(f".{self.destination.name}.{os.getpid()}.tmp")
        try:
            with self.source.open("rb") as src, temporary.open("xb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.destination)
            self._created = True
        finally:
            if temporary.exists():
                temporary.unlink()

    def rollback(self) -> None:
        if self._created and self.destination.exists() and not self.destination.is_symlink():
            self.destination.unlink()
            self._created = False

    def commit(self) -> None:
        self._created = False


class SystemdSshServiceAdapter:
    def __init__(self, *, host: str, unit: str, account: str, runner: CommandRunner = _run) -> None:
        token = re.compile(r"[A-Za-z0-9_.@-]+")
        if not all(token.fullmatch(value) for value in (host, unit, account)):
            raise LifecycleBlockedError("systemd_identity_invalid", "remote service identity is invalid")
        self.host = host
        self.unit = unit
        self.account = account
        self.runner = runner

    def _argv(self, action: str, *extra: str) -> tuple[str, ...]:
        return (
            "/usr/bin/ssh",
            self.host,
            "sudo",
            "-n",
            "-u",
            self.account,
            "systemctl",
            "--user",
            action,
            self.unit,
            *extra,
        )

    def preflight(self) -> ServicePreflight:
        result = self.runner(self._argv("show", "--property=ActiveState,SubState,MainPID"))
        running = result.returncode == 0 and "ActiveState=active" in result.stdout and "SubState=running" in result.stdout
        if result.returncode == 0 and not running:
            raise LifecycleBlockedError("systemd_state_ambiguous", "remote service state is not running")
        return ServicePreflight(running, "running" if running else "inactive")

    def close(self) -> None:
        if self.runner(self._argv("stop")).returncode != 0:
            raise LifecycleBlockedError("systemd_close_failed", "remote service failed to close")

    def closed(self) -> bool:
        result = self.runner(self._argv("show", "--property=ActiveState,SubState,MainPID"))
        return result.returncode != 0 or "ActiveState=inactive" in result.stdout

    def open(self) -> None:
        if self.runner(self._argv("start")).returncode != 0:
            raise LifecycleBlockedError("systemd_open_failed", "remote service failed to open")


def promote_with_service(
    *,
    pointers: PairedPointers | PairedSymlinks,
    adapter: ServiceAdapter,
    release: str,
    profile: str,
    generation: int,
    approval: ApprovalReceipt,
    release_id: str | None = None,
    health: Callable[[], bool],
    observation_timeout_seconds: float = 15.0,
) -> ServicePromotionResult:
    if not adapter.preflight().running:
        raise LifecycleBlockedError("service_not_running", "service preflight is not running")
    adapter.close()
    if not adapter.closed():
        raise LifecycleBlockedError("service_not_closed", "service remained running during promotion")
    if isinstance(pointers, PairedSymlinks):
        if (
            approval.expires_at <= datetime.now(timezone.utc)
            or not approval.digest
            or not release_id
            or approval.bundle_id != release_id
        ):
            raise LifecycleBlockedError("promotion_approval_invalid", "promotion approval does not bind the release")
        previous = pointers.read_pair()
        pointers.switch(Path(release), Path(profile), generation, service_closed=True)
        promotion = PromotionReceipt(*previous, release, profile, generation)
    else:
        promotion = promote_pair(
            pointers,
            release,
            profile,
            generation,
            approval,
            service_closed=True,
        )
    adapter.open()
    deadline = time.monotonic() + observation_timeout_seconds
    healthy = False
    while time.monotonic() < deadline:
        try:
            if adapter.preflight().running and health():
                healthy = True
                break
        except LifecycleBlockedError:
            pass
        time.sleep(0.1)
    if healthy:
        return ServicePromotionResult("PROMOTED_CLEAR")
    adapter.close()
    if not adapter.closed():
        raise LifecycleBlockedError("rollback_service_not_closed", "service remained running before rollback")
    if isinstance(pointers, PairedSymlinks):
        pointers.switch(
            Path(promotion.previous_release),
            Path(promotion.previous_profile),
            promotion.previous_generation,
            service_closed=True,
        )
    else:
        rollback_pair(pointers, promotion, RollbackMode.PRE_TRAFFIC, delta_reconciled=False)
    adapter.open()
    deadline = time.monotonic() + observation_timeout_seconds
    recovered = False
    while time.monotonic() < deadline:
        try:
            if adapter.preflight().running:
                recovered = True
                break
        except LifecycleBlockedError:
            pass
        time.sleep(0.1)
    if not recovered:
        raise LifecycleBlockedError("rollback_service_failed", "service did not recover after rollback")
    return ServicePromotionResult("ROLLED_BACK_PRE_TRAFFIC")


def transition_with_service(
    *,
    pointers: PairedSymlinks,
    legacy_adapter: ServiceAdapter,
    candidate_adapter: ServiceAdapter,
    release: str,
    profile: str,
    generation: int,
    approval: ApprovalReceipt,
    release_id: str,
    health: Callable[[], bool],
    definition_transaction: LaunchdDefinitionTransaction | None = None,
    observation_timeout_seconds: float = 15.0,
) -> ServicePromotionResult:
    """Replace a legacy service with a candidate unit and roll back pre-traffic failures."""
    if (
        approval.expires_at <= datetime.now(timezone.utc)
        or not approval.digest
        or approval.bundle_id != release_id
    ):
        raise LifecycleBlockedError("promotion_approval_invalid", "promotion approval does not bind the release")
    if not legacy_adapter.preflight().running:
        raise LifecycleBlockedError("legacy_service_not_running", "legacy service is not running")
    if definition_transaction is not None:
        definition_transaction.prepare()
    try:
        candidate_running = candidate_adapter.preflight().running
    except Exception:
        if definition_transaction is not None:
            definition_transaction.rollback()
        raise
    if candidate_running:
        if definition_transaction is not None:
            definition_transaction.rollback()
        raise LifecycleBlockedError("candidate_service_already_running", "candidate service is already running")

    try:
        previous = pointers.read_pair()
    except Exception:
        if definition_transaction is not None:
            definition_transaction.rollback()
        raise
    switched = False
    candidate_opened = False
    legacy_closed = False
    try:
        legacy_adapter.close()
        if not legacy_adapter.closed():
            raise LifecycleBlockedError("legacy_service_not_closed", "legacy service remained running")
        legacy_closed = True
        pointers.switch(Path(release), Path(profile), generation, service_closed=True)
        switched = True
        candidate_adapter.open()
        candidate_opened = True
        deadline = time.monotonic() + observation_timeout_seconds
        while time.monotonic() < deadline:
            try:
                if candidate_adapter.preflight().running and health():
                    if definition_transaction is not None:
                        definition_transaction.commit()
                    return ServicePromotionResult("PROMOTED_CLEAR")
            except LifecycleBlockedError:
                pass
            time.sleep(0.1)
    except Exception:
        # The rollback path below handles both expected gate failures and errors.
        pass

    if not legacy_closed:
        if definition_transaction is not None:
            definition_transaction.rollback()
        raise LifecycleBlockedError("transition_close_failed", "legacy service could not be safely closed")
    if candidate_opened or not candidate_adapter.closed():
        candidate_adapter.close()
        if not candidate_adapter.closed():
            raise LifecycleBlockedError("rollback_candidate_not_closed", "candidate service remained running")
    if switched:
        pointers.switch(Path(previous[0]), Path(previous[1]), previous[2], service_closed=True)
    else:
        pointers.recover(service_closed=True)
    legacy_adapter.open()
    deadline = time.monotonic() + observation_timeout_seconds
    while time.monotonic() < deadline:
        try:
            if legacy_adapter.preflight().running:
                if definition_transaction is not None:
                    definition_transaction.rollback()
                return ServicePromotionResult("ROLLED_BACK_PRE_TRAFFIC")
        except LifecycleBlockedError:
            pass
        time.sleep(0.1)
    raise LifecycleBlockedError("rollback_legacy_service_failed", "legacy service did not recover after rollback")
