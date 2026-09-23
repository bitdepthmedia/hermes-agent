from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import ik_lifecycle.closed_runtime as closed_runtime
from ik_lifecycle.closed_runtime import (
    BoundExecutableLoopbackSandbox,
    ClosedRuntimeError,
    build_execution_approval,
    resolve_opaque_handles,
    validate_execution_approval,
    validate_execution_receipt,
)
from ik_lifecycle.ernie_canary import LoopbackProof


PLAN_SHA = "1" * 64
SELECTION_SHA = "2" * 64
IMPLEMENTATION_SHA = "3" * 40
EXECUTOR_SHA = "4" * 64
MODULE_SHA = "5" * 64
OVERLAY_SHA = "6" * 64


def _profile(tmp_path: Path) -> tuple[Path, str, str]:
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    secret = "IK_FIXTURE_SECRET_7f8e9d0c"
    identity = "fixture-ernie-local"
    env_file = profile / ".env"
    env_file.write_text(f"OPENAI_API_KEY={secret}\nSECOND_TOKEN=fixture-token-12345\n", encoding="utf-8")
    env_file.chmod(0o600)
    config = profile / "config.yaml"
    config.write_text(
        json.dumps(
            {
                "mcp_servers": {
                    "nate_os": {
                        "command": "/fixture/bin/python",
                        "args": ["server.py", "--root", "/fixture/root", "--state-dir", "/fixture/state", "--agent-id", identity],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config.chmod(0o644)
    return profile, secret, identity


def test_opaque_handles_are_bound_without_values_or_paths_and_validate_after_use(tmp_path: Path) -> None:
    profile, secret, identity = _profile(tmp_path)
    handles = resolve_opaque_handles(profile, hmac_key=b"k" * 32)

    receipt = handles.safe_receipt()
    rendered = json.dumps(receipt, sort_keys=True)
    assert {item["class"] for item in receipt["handles"]} == {
        "ernie_profile_secret_bundle",
        "nate_os_local_agent_identity",
    }
    assert secret not in rendered
    assert identity not in rendered
    assert str(profile) not in rendered
    assert all(item["resolved"] is True for item in receipt["handles"])

    environment = handles.materialize_environment({"HOME": "/isolated", "HERMES_HOME": "/isolated/profile"})
    assert environment["OPENAI_API_KEY"] == secret
    assert environment["NATE_OS_AGENT_ID"] == identity
    handles.validate_unchanged()


def test_handle_validation_rejects_concurrent_secret_mutation(tmp_path: Path) -> None:
    profile, _, _ = _profile(tmp_path)
    handles = resolve_opaque_handles(profile, hmac_key=b"k" * 32)
    (profile / ".env").write_text("OPENAI_API_KEY=changed-fixture-secret\n", encoding="utf-8")

    with pytest.raises(ClosedRuntimeError, match="opaque_handle_drift"):
        handles.validate_unchanged()


def test_identical_duplicate_secret_assignment_is_accepted_but_conflicting_duplicate_is_rejected(tmp_path: Path) -> None:
    profile, secret, _ = _profile(tmp_path)
    env_file = profile / ".env"
    env_file.write_text(env_file.read_text(encoding="utf-8") + f"OPENAI_API_KEY={secret}\n", encoding="utf-8")
    env_file.chmod(0o600)
    assert resolve_opaque_handles(profile, hmac_key=b"k" * 32).safe_receipt()["status"] == "CLEAR"

    env_file.write_text(env_file.read_text(encoding="utf-8") + "OPENAI_API_KEY=conflicting-fixture-value\n", encoding="utf-8")
    with pytest.raises(ClosedRuntimeError, match="opaque_secret_bundle_invalid"):
        resolve_opaque_handles(profile, hmac_key=b"k" * 32)


@pytest.mark.parametrize("failure", ["symlink", "unsafe_mode", "control_key", "ambiguous_identity"])
def test_handle_resolution_fails_closed_on_unsafe_or_ambiguous_sources(tmp_path: Path, failure: str) -> None:
    profile, _, identity = _profile(tmp_path)
    if failure == "symlink":
        target = profile / "secret-target"
        target.write_text("OPENAI_API_KEY=fixture-secret\n", encoding="utf-8")
        target.chmod(0o600)
        (profile / ".env").unlink()
        (profile / ".env").symlink_to(target)
    elif failure == "unsafe_mode":
        (profile / ".env").chmod(0o644)
    elif failure == "control_key":
        (profile / ".env").write_text("HOME=/unexpected\n", encoding="utf-8")
        (profile / ".env").chmod(0o600)
    else:
        config = json.loads((profile / "config.yaml").read_text(encoding="utf-8"))
        config["mcp_servers"]["nate_os"]["args"].extend(["--agent-id", identity])
        (profile / "config.yaml").write_text(json.dumps(config), encoding="utf-8")

    with pytest.raises(ClosedRuntimeError):
        resolve_opaque_handles(profile, hmac_key=b"k" * 32)


def test_secret_log_scan_detects_values_without_returning_them(tmp_path: Path) -> None:
    profile, secret, identity = _profile(tmp_path)
    handles = resolve_opaque_handles(profile, hmac_key=b"k" * 32)
    clear = tmp_path / "clear.log"
    clear.write_text("runtime healthy\n", encoding="utf-8")
    leaked = tmp_path / "leaked.log"
    leaked.write_text(f"bad output {secret}\n", encoding="utf-8")
    public_collision = tmp_path / "public-collision.log"
    public_collision.write_text(f"public fixture mentions {identity}\n", encoding="utf-8")

    assert handles.secret_leak_count((clear,)) == 0
    assert handles.secret_leak_count((leaked,)) == 1
    assert handles.secret_leak_count((public_collision,)) == 0
    assert handles.identity_leak_count((public_collision,)) == 1


def test_short_sensitive_credential_is_rejected_instead_of_becoming_a_weak_leak_sentinel(tmp_path: Path) -> None:
    profile, _, _ = _profile(tmp_path)
    (profile / ".env").write_text("OPENAI_API_KEY=short\n", encoding="utf-8")

    with pytest.raises(ClosedRuntimeError, match="opaque_secret_bundle_invalid"):
        resolve_opaque_handles(profile, hmac_key=b"k" * 32)


def test_execution_approval_is_exact_digest_bound_and_excludes_live_private_surfaces() -> None:
    approval = build_execution_approval(
        plan_sha256=PLAN_SHA,
        selection_sha256=SELECTION_SHA,
        implementation_commit=IMPLEMENTATION_SHA,
        executor_sha256=EXECUTOR_SHA,
        module_sha256=MODULE_SHA,
        overlay_manifest_sha256=OVERLAY_SHA,
    )

    assert validate_execution_approval(
        approval,
        plan_sha256=PLAN_SHA,
        selection_sha256=SELECTION_SHA,
        implementation_commit=IMPLEMENTATION_SHA,
        executor_sha256=EXECUTOR_SHA,
        module_sha256=MODULE_SHA,
        overlay_manifest_sha256=OVERLAY_SHA,
    ) == approval["sha256"]
    assert approval["approval"]["scope"]["public_synthetic_only"] is True
    assert approval["approval"]["scope"]["private_content"] is False
    assert approval["approval"]["scope"]["live_or_external_state"] is False

    drifted = copy.deepcopy(approval)
    drifted["approval"]["plan_sha256"] = "f" * 64
    with pytest.raises(ClosedRuntimeError, match="closed_runtime_approval_digest_mismatch"):
        validate_execution_approval(
            drifted,
            plan_sha256=PLAN_SHA,
            selection_sha256=SELECTION_SHA,
            implementation_commit=IMPLEMENTATION_SHA,
            executor_sha256=EXECUTOR_SHA,
            module_sha256=MODULE_SHA,
            overlay_manifest_sha256=OVERLAY_SHA,
        )


def test_v2_execution_approval_explicitly_limits_clone_use_to_isolated_runtime() -> None:
    assert "migration_clone_runtime" in __import__("inspect").signature(build_execution_approval).parameters
    approval = build_execution_approval(
        plan_sha256=PLAN_SHA,
        selection_sha256=SELECTION_SHA,
        implementation_commit=IMPLEMENTATION_SHA,
        executor_sha256=EXECUTOR_SHA,
        module_sha256=MODULE_SHA,
        overlay_manifest_sha256=OVERLAY_SHA,
        migration_clone_runtime=True,
    )

    assert approval["approval"]["scope"] == {
        "public_synthetic_only": True,
        "migration_clone_runtime": "isolated_startup_only",
        "private_content": "no_model_or_log_exposure",
        "live_or_external_state": False,
        "bert": False,
        "promotion": False,
        "automation": False,
    }
    assert validate_execution_approval(
        approval,
        plan_sha256=PLAN_SHA,
        selection_sha256=SELECTION_SHA,
        implementation_commit=IMPLEMENTATION_SHA,
        executor_sha256=EXECUTOR_SHA,
        module_sha256=MODULE_SHA,
        overlay_manifest_sha256=OVERLAY_SHA,
        migration_clone_runtime=True,
    ) == approval["sha256"]


def test_aggregate_clone_binding_is_redacted_and_fails_on_clone_or_rollback_drift(tmp_path: Path) -> None:
    assert hasattr(closed_runtime, "resolve_clone_runtime_binding")
    storage = tmp_path / "continuity"
    migrated = storage / "rehearsals" / "rehearsal-fixture" / "migrated"
    rollback = storage / "backups" / "snapshot-fixture" / "snapshot.enc"
    migrated.mkdir(parents=True, mode=0o700)
    rollback.parent.mkdir(parents=True, mode=0o700)
    private = migrated / "state.db"
    private.write_bytes(b"private-fixture-state")
    private.chmod(0o600)
    rollback.write_bytes(b"encrypted-rollback-fixture")
    rollback.chmod(0o400)
    migrated_tree, _, _ = closed_runtime._tree_digest(migrated)
    rollback_sha = __import__("hashlib").sha256(rollback.read_bytes()).hexdigest()
    semantic = {
        "status": "CLEAR",
        "rehearsal_id": "rehearsal-fixture",
        "migrated_tree_sha256": migrated_tree,
    }
    snapshot = {
        "status": "CLEAR",
        "snapshot_id": "snapshot-fixture",
        "archive_sha256": rollback_sha,
    }

    binding = closed_runtime.resolve_clone_runtime_binding(storage, semantic, snapshot)
    rendered = json.dumps(binding.safe_receipt(), sort_keys=True)
    assert binding.safe_receipt() == {
        "status": "CLEAR",
        "migrated_tree_sha256": migrated_tree,
        "rollback_artifact_sha256": rollback_sha,
        "aggregate_file_count": 1,
        "aggregate_bytes": len(b"private-fixture-state"),
    }
    assert str(storage) not in rendered
    assert "private-fixture-state" not in rendered

    private.write_bytes(b"changed-private-fixture-state")
    with pytest.raises(ClosedRuntimeError, match="closed_runtime_migration_clone_drift"):
        binding.validate_unchanged()


def test_execution_receipt_rejects_private_fields_paths_and_nonclear_gates() -> None:
    receipt = {
        "schema_id": "ik.hermes.credential-bound-closed-runtime-receipt.v1",
        "status": "CLEAR_CLOSED_RUNTIME_ONLY",
        "bindings": {"plan_sha256": PLAN_SHA, "selection_sha256": SELECTION_SHA},
        "credential_handles": {"resolved": 2, "leak_count": 0},
        "process_separation": {"model_credential_keys": 0, "model_identity": False},
        "network": {"model_worker": "CLEAR", "ernie_cell": "CLEAR", "external_access": False},
        "model_evaluation": {"passed": 12, "total": 12, "concurrency_passed": 2, "concurrency_total": 2},
        "ernie_cell": {"startups": 2, "restarts": 1, "health_checks": 6},
        "rollback": {"rp2": "CLEAR", "rp3_crash_recovery": "CLEAR", "rp3_pretraffic": "CLEAR"},
        "live_effects": False,
    }
    assert validate_execution_receipt(receipt) is True

    for key, value in (("private_content", "fixture"), ("path", "/Users/fixture/private")):
        invalid = copy.deepcopy(receipt)
        invalid[key] = value
        with pytest.raises(ClosedRuntimeError, match="closed_runtime_receipt_privacy_invalid"):
            validate_execution_receipt(invalid)

    invalid = copy.deepcopy(receipt)
    invalid["model_evaluation"]["passed"] = 11
    with pytest.raises(ClosedRuntimeError, match="closed_runtime_receipt_gate_failed"):
        validate_execution_receipt(invalid)


def test_v2_receipt_requires_unchanged_clone_and_disposable_runtime_profile() -> None:
    receipt = {
        "schema_id": "ik.hermes.credential-bound-closed-runtime-receipt.v2",
        "status": "CLEAR_CLOSED_RUNTIME_ONLY",
        "bindings": {"plan_sha256": PLAN_SHA, "selection_sha256": SELECTION_SHA},
        "credential_handles": {"resolved": 2, "leak_count": 0},
        "process_separation": {"model_credential_keys": 0, "model_identity": False, "private_prompt_count": 0},
        "network": {"model_worker": "CLEAR", "ernie_cell": "CLEAR", "external_access": False},
        "model_evaluation": {"passed": 12, "total": 12, "concurrency_passed": 2, "concurrency_total": 2},
        "ernie_cell": {"startups": 2, "restarts": 1, "health_checks": 6},
        "continuity": {"migration_clone": "CLEAR_UNCHANGED", "runtime_profile": "CLEAR_DISPOSABLE"},
        "continuity_aggregate": {
            "runtime_files": 12,
            "runtime_bytes": 4096,
            "excluded_configuration": 2,
            "excluded_credentials": 2,
            "excluded_schedules": 7,
            "excluded_execution_surfaces": 4,
        },
        "rollback": {"immutable_backup": "CLEAR_UNCHANGED", "rp2": "CLEAR", "rp3_crash_recovery": "CLEAR", "rp3_pretraffic": "CLEAR"},
        "live_effects": False,
    }
    assert validate_execution_receipt(receipt) is True

    for field in ("migration_clone", "runtime_profile"):
        invalid = copy.deepcopy(receipt)
        invalid["continuity"][field] = "BLOCKED"
        with pytest.raises(ClosedRuntimeError, match="closed_runtime_receipt_gate_failed"):
            validate_execution_receipt(invalid)

    missing_aggregate = copy.deepcopy(receipt)
    del missing_aggregate["continuity_aggregate"]
    with pytest.raises(ClosedRuntimeError, match="closed_runtime_receipt_gate_failed"):
        validate_execution_receipt(missing_aggregate)


def test_closed_runtime_entrypoint_resolves_repo_imports_outside_repo_workdir(tmp_path: Path) -> None:
    runner = Path(__file__).resolve().parents[2] / "scripts/ik-ernie-closed-runtime"
    completed = subprocess.run(
        (sys.executable, str(runner), "--help"),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "--execute" in completed.stdout


@pytest.mark.skipif(sys.platform != "darwin", reason="sandbox-exec is a macOS boundary")
def test_non_python_executable_is_bound_to_a_fresh_python_network_proof(tmp_path: Path) -> None:
    executable = tmp_path / "worker"
    executable.write_bytes(b"worker-v1")
    executable.chmod(0o700)
    now = datetime.now(timezone.utc)

    class FakeProbe:
        def create_proof(self, proof_path: Path, *, ttl_seconds: int) -> LoopbackProof:
            proof_path.write_text("fixture", encoding="utf-8")
            return LoopbackProof("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64, now, now + timedelta(seconds=ttl_seconds), proof_path)

        def validate(self, proof_path: Path) -> LoopbackProof:
            return LoopbackProof("a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64, now, now + timedelta(seconds=300), proof_path)

    sandbox = BoundExecutableLoopbackSandbox(FakeProbe(), executable, sandbox_exec=Path("/usr/bin/sandbox-exec"))
    proof = sandbox.create_proof(tmp_path / "bound-proof.json", ttl_seconds=300)
    assert sandbox.wrap((str(executable), "serve"), proof)[-2:] == (str(executable), "serve")

    executable.write_bytes(b"worker-v2")
    with pytest.raises(ClosedRuntimeError, match="closed_runtime_network_target_drift"):
        sandbox.wrap((str(executable), "serve"), proof)
