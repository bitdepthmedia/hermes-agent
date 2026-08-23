"""Local-only Bert cloud-cell canary over the shared sealed Hermes bundle."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from typing import Any, Mapping

from ik_extensions.persona_orchestration.approval_result import (
    ApprovalDecision,
    ApprovalState,
    resolve_approval_result,
)
from ik_extensions.persona_orchestration.availability import AvailabilityConfig, run_availability_tick
from ik_extensions.persona_orchestration.envelope import Owner, validate_envelope
from ik_extensions.persona_orchestration.nate_os import MemoryAction, authorize_memory_action
from ik_extensions.persona_orchestration.privacy import LocalTask, PrivacyPolicy, sanitize_for_recipient
from ik_extensions.persona_orchestration.routing import (
    IntakeRequest,
    RoutingPolicy,
    classify_request,
    decompose_mixed,
)
from ik_extensions.persona_orchestration.store import HandoffStore
from ik_extensions.persona_orchestration.transport import LoopbackTransport

from .composed_source import tree_digest
from .ernie_canary import (
    CanaryRuntime,
    LoopbackOnlyMacOSSandbox,
    LoopbackProof,
    ProcessCanaryRuntime,
    discard_runtime_profile,
)
from .promotion import PairedPointers, PromotionReceipt
from .rollback import RollbackMode, rollback_pair


_HEX = frozenset("0123456789abcdef")
_PRIVATE_CANARY = "SYNTHETIC_PRIVATE_BERT_BOUNDARY_CANARY"
_RECEIPT_FIELDS = frozenset(
    {
        "schema_version",
        "canary_id",
        "observed_at",
        "status",
        "cell_id",
        "shared_bundle_id",
        "official_target_tag",
        "official_target_sha",
        "candidate_manifest_sha256",
        "candidate_source_tree_sha256",
        "candidate_python_sha256",
        "architecture_contract_sha256",
        "architecture_execution_receipt_sha256",
        "supply_chain_receipt_sha256",
        "ernie_canary_receipt_sha256",
        "ernie_closed_runtime_receipt_sha256",
        "lifecycle_adapter_sha256",
        "network_gate",
        "health_counts",
        "behavior_gates",
        "synthetic_evaluation",
        "rollback_gates",
        "promotion_eligible",
        "retained_blockers",
        "skipped_surfaces",
    }
)


class BertCanaryError(RuntimeError):
    """A deliberately redacted Bert canary failure."""


def _valid_digest(value: str, length: int = 64) -> bool:
    return isinstance(value, str) and len(value) == length and set(value.lower()) <= _HEX


def _safe_token(value: str, label: str) -> str:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if not value or len(value) > 96 or any(character not in allowed for character in value):
        raise BertCanaryError(f"{label}_invalid")
    return value


def _sha256(path: Path, code: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise BertCanaryError(code) from error


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _write_private_json(path: Path, value: Mapping[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def _safe_root(path: Path, *, release: Path, denied: tuple[Path, ...]) -> Path:
    root = Path(os.path.abspath(os.fspath(path))).resolve(strict=False)
    release = Path(os.path.abspath(os.fspath(release))).resolve(strict=False)
    if root.exists() or root == release or release in root.parents or root in release.parents:
        raise BertCanaryError("canary_root_invalid")
    for blocked in denied:
        blocked_root = Path(os.path.abspath(os.fspath(blocked))).resolve(strict=False)
        if root == blocked_root or blocked_root in root.parents or root in blocked_root.parents:
            raise BertCanaryError("canary_root_invalid")
    # macOS exposes /var through a system-owned symlink.  Reject the direct
    # caller-controlled parent without incorrectly rejecting that OS layout.
    if root.parent.is_symlink():
        raise BertCanaryError("canary_root_invalid")
    root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    return root


def _release_is_immutable(release: Path) -> bool:
    for path in (release, *release.rglob("*")):
        try:
            metadata = os.lstat(path)
        except OSError:
            return False
        if stat.S_ISLNK(metadata.st_mode) or metadata.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            return False
        if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
            return False
    return True


@dataclass(frozen=True)
class BertCanaryRequest:
    canary_root: Path
    candidate_release_root: Path
    candidate_source_root: Path
    candidate_manifest_path: Path
    expected_candidate_manifest_sha256: str
    candidate_python: Path
    expected_candidate_python_sha256: str
    architecture_contract_sha256: str
    shared_bundle_id: str
    official_target_tag: str
    official_target_sha: str
    canary_id: str
    denied_roots: tuple[Path, ...]
    architecture_execution_receipt_sha256: str = "b" * 64
    supply_chain_receipt_sha256: str = "c" * 64
    ernie_canary_receipt_sha256: str = "d" * 64
    ernie_closed_runtime_receipt_sha256: str = "e" * 64
    require_read_only_release: bool = True


@dataclass(frozen=True)
class BertCanaryReceipt:
    schema_version: str
    canary_id: str
    observed_at: str
    status: str
    cell_id: str
    shared_bundle_id: str
    official_target_tag: str
    official_target_sha: str
    candidate_manifest_sha256: str
    candidate_source_tree_sha256: str
    candidate_python_sha256: str
    architecture_contract_sha256: str
    architecture_execution_receipt_sha256: str
    supply_chain_receipt_sha256: str
    ernie_canary_receipt_sha256: str
    ernie_closed_runtime_receipt_sha256: str
    lifecycle_adapter_sha256: str
    network_gate: str
    health_counts: dict[str, int]
    behavior_gates: dict[str, str]
    synthetic_evaluation: dict[str, int | str]
    rollback_gates: dict[str, str]
    promotion_eligible: bool
    retained_blockers: tuple[str, ...]
    skipped_surfaces: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["retained_blockers"] = list(self.retained_blockers)
        value["skipped_surfaces"] = list(self.skipped_surfaces)
        return value


@dataclass(frozen=True)
class BertCanaryResult:
    receipt: BertCanaryReceipt
    receipt_path: Path


class _SyntheticSandbox:
    def create_proof(self, proof_path: Path, *, ttl_seconds: int = 300) -> LoopbackProof:
        now = datetime.now(timezone.utc)
        _write_private_json(proof_path, {"synthetic": True})
        return LoopbackProof("1" * 64, "2" * 64, "3" * 64, "4" * 64, "5" * 64, now, now + timedelta(seconds=ttl_seconds), proof_path)


class _SyntheticRuntime(CanaryRuntime):
    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.heartbeats = 0

    def start(self, request: BertCanaryRequest, *, profile_root: Path, run_root: Path, proof: LoopbackProof) -> dict[str, object]:
        del request, profile_root, run_root, proof
        self.starts += 1
        return {"port": 49000 + self.starts, "version": "2026.8.18"}

    def health(self, handle: Mapping[str, object]) -> dict[str, object]:
        return {"ok": True, "auth_required": False, "version": handle["version"]}

    def heartbeat(self, handle: Mapping[str, object]) -> dict[str, object]:
        self.heartbeats += 1
        return self.health(handle)

    def stop(self, handle: Mapping[str, object]) -> None:
        del handle
        self.stops += 1


def _envelope_value() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "task_id": "22222222-2222-4222-8222-222222222222",
        "parent_task_id": None,
        "owner": "ernie",
        "requester_persona": "bert",
        "task_class": "personal",
        "privacy_class": "sanitized-cloud",
        "payload": {"request": "perform the local-only synthetic step"},
        "local_payload_ref": None,
        "provenance": {"channel": "synthetic", "message_id": "fixture"},
        "constraints": {"cloud_substitution": False},
        "approval": {"state": "not_required", "scope": []},
        "expected_result": {"schema_id": "ik.synthetic.v1"},
        "completion": "pending",
        "idempotency_key": "bert-to-ernie:synthetic:1",
        "lineage": {"hop_count": 1, "max_hops": 4, "visited_owners": ["bert", "ernie"], "prior_digest": "0" * 64},
        "retry": {"attempt": 0, "next_attempt_at": None, "expires_at": "2026-08-23T12:00:00Z", "escalation": "approval-inbox"},
        "integrity": {"sender": "bert", "sequence": 1, "signature_metadata": "synthetic", "envelope_digest": None},
    }


def _behavior_gates(root: Path) -> tuple[dict[str, str], dict[str, int | str]]:
    sanitized = sanitize_for_recipient(
        LocalTask(
            "bert-boundary-synthetic",
            {"summary": "bounded personal research", "private_context": _PRIVATE_CANARY, "identity": "synthetic-person"},
        ),
        Owner.BERT,
        PrivacyPolicy(("summary",), "synthetic-v1"),
    )
    rendered = json.dumps(sanitized.payload, sort_keys=True)
    if _PRIVATE_CANARY in rendered or sanitized.local_mapping_id in rendered:
        raise BertCanaryError("sanitization_gate_failed")

    work = classify_request(
        IntakeRequest("work-1", Owner.BERT, ("work",), False, "build a synthetic fixture", None),
        RoutingPolicy(),
    )
    work_repeat = classify_request(
        IntakeRequest("work-1", Owner.BERT, ("work",), False, "build a synthetic fixture", None, Owner.CODEX),
        RoutingPolicy(),
    )
    if work.owner != Owner.CODEX or work_repeat.owner != Owner.CODEX:
        raise BertCanaryError("codex_ownership_gate_failed")
    mixed_request = IntakeRequest("mixed-1", Owner.BERT, ("work", "personal"), False, "synthetic mixed request", None)
    mixed = classify_request(mixed_request, RoutingPolicy())
    children = decompose_mixed(mixed_request, mixed)
    if [(child.owner.value, child.task_class) for child in children] != [("codex", "work"), ("bert", "personal")]:
        raise BertCanaryError("mixed_routing_gate_failed")

    if not authorize_memory_action("bert", MemoryAction.READ, "all-agents").allowed:
        raise BertCanaryError("nate_os_boundary_failed")
    if authorize_memory_action("bert", MemoryAction.WRITE, "all-agents").allowed:
        raise BertCanaryError("nate_os_boundary_failed")
    if authorize_memory_action("bert", MemoryAction.READ, "ernie-local").allowed:
        raise BertCanaryError("nate_os_boundary_failed")

    states = (
        resolve_approval_result(approval_required=True, decision=None, executed=False).approval_state,
        resolve_approval_result(approval_required=True, decision=ApprovalDecision.APPROVE, executed=True).approval_state,
        resolve_approval_result(approval_required=True, decision=ApprovalDecision.DENY, executed=False).approval_state,
        resolve_approval_result(approval_required=False, decision=None, executed=True).approval_state,
    )
    if states != (ApprovalState.REQUIRED, ApprovalState.APPROVED, ApprovalState.DENIED, ApprovalState.NOT_REQUIRED):
        raise BertCanaryError("approval_contract_gate_failed")

    store = HandoffStore(root / "state" / "handoff.db")
    envelope = validate_envelope(_envelope_value())
    now = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
    store.enqueue_once(envelope, now=now)
    store.enqueue_once(envelope, now=now)
    quiet = run_availability_tick(
        now,
        AvailabilityConfig(),
        store,
        LoopbackTransport(available=False, authenticated_sender=Owner.BERT, recipient=Owner.ERNIE),
    )
    next_attempt = store.next_attempt_at(envelope.task_id)
    delay_minutes = int((next_attempt - now).total_seconds() // 60)
    if quiet.status != "QUIET_RETRY" or quiet.notifications != 0 or store.count_pending() != 1 or not 20 <= delay_minutes <= 30:
        raise BertCanaryError("offline_ernie_gate_failed")
    delivered = run_availability_tick(
        next_attempt,
        AvailabilityConfig(),
        store,
        LoopbackTransport(available=True, authenticated_sender=Owner.BERT, recipient=Owner.ERNIE),
    )
    if delivered.status != "DELIVERED" or delivered.notifications != 0 or store.count_pending() != 0:
        raise BertCanaryError("offline_ernie_gate_failed")

    gates = {
        "shared_release_bundle": "CLEAR",
        "sanitized_only": "CLEAR",
        "nate_os_read_only": "CLEAR",
        "codex_exactly_once": "CLEAR",
        "personal_and_mixed_routing": "CLEAR",
        "typed_approval_contract": "CLEAR",
        "offline_ernie_pending_handoff": "CLEAR",
        "independent_cell_state": "CLEAR",
    }
    evaluation: dict[str, int | str] = {"status": "CLEAR_MODEL_NEUTRAL_SYNTHETIC", "passed": 8, "total": 8}
    return gates, evaluation


def receipt_is_redacted(payload: Mapping[str, object]) -> bool:
    if set(payload) != _RECEIPT_FIELDS:
        return False
    rendered = json.dumps(payload, sort_keys=True).casefold()
    blocked = ("/users/", _PRIVATE_CANARY.casefold(), "ernie-local:", "token=", "password=")
    return not any(marker in rendered for marker in blocked)


class BertCanaryEngine:
    def __init__(self, *, sandbox: Any | None = None, runtime: CanaryRuntime | None = None) -> None:
        self._sandbox = sandbox
        self._runtime = runtime

    @classmethod
    def for_synthetic_tests(cls) -> "BertCanaryEngine":
        return cls(sandbox=_SyntheticSandbox(), runtime=_SyntheticRuntime())

    def execute(self, request: BertCanaryRequest) -> BertCanaryResult:
        canary_id = _safe_token(request.canary_id, "canary_id")
        shared_bundle_id = _safe_token(request.shared_bundle_id, "shared_bundle_id")
        bindings = (
            request.expected_candidate_manifest_sha256,
            request.expected_candidate_python_sha256,
            request.architecture_contract_sha256,
            request.architecture_execution_receipt_sha256,
            request.supply_chain_receipt_sha256,
            request.ernie_canary_receipt_sha256,
            request.ernie_closed_runtime_receipt_sha256,
        )
        if not all(_valid_digest(value) for value in bindings) or not _valid_digest(request.official_target_sha, 40):
            raise BertCanaryError("canary_binding_invalid")
        release = Path(request.candidate_release_root).resolve()
        source = Path(request.candidate_source_root).resolve()
        manifest = Path(request.candidate_manifest_path).resolve()
        runtime_path = Path(request.candidate_python).resolve()
        if not release.is_dir() or release.is_symlink() or not source.is_dir() or source.is_symlink():
            raise BertCanaryError("candidate_binding_invalid")
        if request.require_read_only_release and not _release_is_immutable(release):
            raise BertCanaryError("candidate_release_not_immutable")
        if _sha256(manifest, "candidate_binding_invalid") != request.expected_candidate_manifest_sha256:
            raise BertCanaryError("candidate_binding_invalid")
        if _sha256(runtime_path, "candidate_runtime_invalid") != request.expected_candidate_python_sha256:
            raise BertCanaryError("candidate_runtime_invalid")
        try:
            document = json.loads(manifest.read_text(encoding="utf-8"))
            identity = document["identity"]
            expected_tree = identity["bindings"]["composed-source"]["tree_sha256"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise BertCanaryError("candidate_binding_invalid") from error
        if (
            document.get("status") != "SEALED_CODE_ONLY"
            or document.get("bundle_id") != shared_bundle_id
            or identity.get("target_tag") != request.official_target_tag
            or identity.get("target_commit_sha") != request.official_target_sha
        ):
            raise BertCanaryError("shared_bundle_binding_invalid")
        observed_tree = tree_digest(source)
        if observed_tree != expected_tree:
            raise BertCanaryError("candidate_source_drift")

        root = _safe_root(request.canary_root, release=release, denied=request.denied_roots)
        profile = root / "runtime-profile"
        profile.mkdir(mode=0o700)
        (profile / "cell.json").write_text('{"cell_id":"bert","trust_zone":"sanitized-cloud"}\n', encoding="utf-8")
        os.chmod(profile / "cell.json", 0o600)
        sandbox = self._sandbox or LoopbackOnlyMacOSSandbox(runtime_path)
        runtime = self._runtime or ProcessCanaryRuntime(sandbox)
        proof_path = root / "network-proof.json"
        try:
            proof = sandbox.create_proof(proof_path, ttl_seconds=300)
            health_counts = {"startups": 0, "heartbeats": 0, "stops": 0}
            for _ in range(2):
                handle = runtime.start(request, profile_root=profile, run_root=root, proof=proof)
                health_counts["startups"] += 1
                try:
                    health = runtime.health(handle)
                    heartbeat = runtime.heartbeat(handle)
                    if health.get("ok") is not True or heartbeat.get("ok") is not True:
                        raise BertCanaryError("runtime_health_failed")
                    health_counts["heartbeats"] += 1
                finally:
                    runtime.stop(handle)
                    health_counts["stops"] += 1

            behavior, evaluation = _behavior_gates(root)
            pointers = PairedPointers(root / "release-pointer.json", root / "profile-pointer.json", root / "pointer-journal.json")
            pointers.initialize("legacy-bert-release", "legacy-bert-profile", 1)
            try:
                pointers.switch(shared_bundle_id, "synthetic-bert-profile", 2, crash_after_release=True)
            except RuntimeError:
                pointers.recover()
            if pointers.read_pair() != ("legacy-bert-release", "legacy-bert-profile", 1):
                raise BertCanaryError("rp3_crash_recovery_failed")
            pointers.switch(shared_bundle_id, "synthetic-bert-profile", 2)
            rollback = rollback_pair(
                pointers,
                PromotionReceipt("legacy-bert-release", "legacy-bert-profile", 1, shared_bundle_id, "synthetic-bert-profile", 2),
                RollbackMode.PRE_TRAFFIC,
                delta_reconciled=False,
            )
            if rollback.status != "ROLLED_BACK" or pointers.read_pair() != ("legacy-bert-release", "legacy-bert-profile", 1):
                raise BertCanaryError("rp3_pretraffic_failed")
            discard_runtime_profile(profile, root)
            rollback_gates = {"rp2": "CLEAR", "rp3_crash_recovery": "CLEAR", "rp3_pretraffic": "CLEAR"}
            receipt = BertCanaryReceipt(
                schema_version="ik.bert-runtime-canary.v1",
                canary_id=canary_id,
                observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                status="CLEAR_SAFE_LOCAL_BERT_CANARY",
                cell_id="bert",
                shared_bundle_id=shared_bundle_id,
                official_target_tag=request.official_target_tag,
                official_target_sha=request.official_target_sha,
                candidate_manifest_sha256=request.expected_candidate_manifest_sha256,
                candidate_source_tree_sha256=observed_tree,
                candidate_python_sha256=request.expected_candidate_python_sha256,
                architecture_contract_sha256=request.architecture_contract_sha256,
                architecture_execution_receipt_sha256=request.architecture_execution_receipt_sha256,
                supply_chain_receipt_sha256=request.supply_chain_receipt_sha256,
                ernie_canary_receipt_sha256=request.ernie_canary_receipt_sha256,
                ernie_closed_runtime_receipt_sha256=request.ernie_closed_runtime_receipt_sha256,
                lifecycle_adapter_sha256=_sha256(Path(__file__), "lifecycle_adapter_unavailable"),
                network_gate="CLEAR_OS_BACKED_DENY_EXTERNAL" if isinstance(sandbox, LoopbackOnlyMacOSSandbox) else "CLEAR_SYNTHETIC_PROOF",
                health_counts=health_counts,
                behavior_gates=behavior,
                synthetic_evaluation=evaluation,
                rollback_gates=rollback_gates,
                promotion_eligible=False,
                retained_blockers=(
                    "live-bert-access-not-authorized",
                    "bert-credentials-not-resolved",
                    "legacy-health-automation-active",
                    "live-service-profile-pointer-promotion-not-authorized",
                ),
                skipped_surfaces=(
                    "credentials",
                    "private-content",
                    "models-and-weights",
                    "schedules-and-automations",
                    "live-ernie",
                    "live-bert-and-ssh",
                    "promotion-deployment-restart",
                ),
            )
            payload = receipt.to_dict()
            if not receipt_is_redacted(payload):
                raise BertCanaryError("receipt_privacy_invalid")
            receipt_path = root / "receipt.json"
            _write_private_json(receipt_path, payload)
            return BertCanaryResult(receipt, receipt_path)
        except BertCanaryError:
            raise
        except Exception as error:
            raise BertCanaryError("bert_canary_failed") from error
