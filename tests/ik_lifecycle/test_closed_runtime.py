from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from ik_lifecycle.closed_runtime import (
    ClosedRuntimeError,
    build_execution_approval,
    resolve_opaque_handles,
    validate_execution_approval,
    validate_execution_receipt,
)


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
    profile, secret, _ = _profile(tmp_path)
    handles = resolve_opaque_handles(profile, hmac_key=b"k" * 32)
    clear = tmp_path / "clear.log"
    clear.write_text("runtime healthy\n", encoding="utf-8")
    leaked = tmp_path / "leaked.log"
    leaked.write_text(f"bad output {secret}\n", encoding="utf-8")

    assert handles.secret_leak_count((clear,)) == 0
    assert handles.secret_leak_count((leaked,)) == 1


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


def test_execution_receipt_rejects_private_fields_paths_and_nonclear_gates() -> None:
    receipt = {
        "schema_id": "ik.hermes.credential-bound-closed-runtime-receipt.v1",
        "status": "CLEAR_CLOSED_RUNTIME_ONLY",
        "bindings": {"plan_sha256": PLAN_SHA, "selection_sha256": SELECTION_SHA},
        "credential_handles": {"resolved": 2, "leak_count": 0},
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
