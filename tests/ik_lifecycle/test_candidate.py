from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ik_lifecycle.candidate import (
    DEFAULT_REPLAY_MANIFEST,
    build_candidate,
    load_replay_manifest,
    seal_candidate,
)
from ik_lifecycle.composed_source import OverlayManifest, compose_source
from ik_lifecycle.filesystem import verify_tree_read_only
from ik_lifecycle.models import CellSpec, GateSet, LifecycleBlockedError, ReleaseSelection, StableRelease
from ik_lifecycle.release_bundle import ArtifactBinding, ReleaseBundleInputs, build_release_bundle


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _source_repo(tmp_path: Path, files: dict[str, str] | None = None) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init")
    _git(source, "config", "user.email", "fixture@example.invalid")
    _git(source, "config", "user.name", "Fixture")
    for relative_path, content in (files or {"README.md": "fixture\n"}).items():
        path = source / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-m", "fixture")
    return source, _git(source, "rev-parse", "HEAD")


def _selection(target_sha: str) -> ReleaseSelection:
    return ReleaseSelection(
        latest=StableRelease(
            "v2026.8.19",
            "f" * 40,
            datetime(2026, 8, 21, 12, 16, 39, tzinfo=timezone.utc),
            "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.8.19",
        ),
        target=StableRelease(
            "v2026.8.18",
            target_sha,
            datetime(2026, 8, 18, 7, 26, 46, tzinfo=timezone.utc),
            "https://github.com/NousResearch/hermes-agent/releases/tag/v2026.8.18",
        ),
        discovered_at=datetime(2026, 8, 21, 17, 55, tzinfo=timezone.utc),
    )


def _cell(cell_id: str, protected_paths: tuple[Path, ...] = ()) -> CellSpec:
    return CellSpec(
        cell_id=cell_id,
        trust_zone="local_private" if cell_id == "ernie" else "cloud_sanitized",
        protected_paths=protected_paths,
        legacy_health_automation_status="ACTIVE",
        computer_history_path_status="approval_required",
    )


def _canonical(document: object) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _write_evidence(path: Path, kind: str, status: str, data: dict[str, object]) -> str:
    body = {
        "data": data,
        "kind": kind,
        "observed_at": "2026-08-21T18:45:00Z",
        "schema_version": 1,
        "status": status,
    }
    document = {"receipt": body, "sha256": hashlib.sha256(_canonical(body)).hexdigest()}
    path.write_bytes(_canonical(document) + b"\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dependency_plan(candidate, *, shared_npm_config: bool = False) -> tuple[Path, dict[str, object]]:
    manifest = json.loads(candidate.manifest_path.read_text())
    audit_source = candidate.path / "dependency-audit" / "source"
    audit_source.mkdir(parents=True)
    config_root = candidate.path / "dependency-audit" / "config"
    config_root.mkdir()
    user_config = config_root / "npm-user.npmrc"
    global_config = user_config if shared_npm_config else config_root / "npm-global.npmrc"
    user_config.write_bytes(b"")
    if global_config != user_config:
        global_config.write_bytes(b"")
    user_config.chmod(0o444)
    global_config.chmod(0o444)
    empty_sha = hashlib.sha256(b"").hexdigest()
    commands = [
        {
            "workdir": str(audit_source),
            "argv": [
                "/usr/bin/env",
                "-i",
                "HOME=/isolated/home",
                f"NPM_CONFIG_USERCONFIG={user_config}",
                f"NPM_CONFIG_GLOBALCONFIG={global_config}",
                "/isolated/bin/npm",
                "ci",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
            ],
        }
    ]
    plan: dict[str, object] = {
        "artifact_map_sha256": hashlib.sha256(_canonical(manifest["supply_chain"]["artifact_sha256"])).hexdigest(),
        "audit_mirror_tree_sha256": manifest["source"]["tree_sha256"],
        "candidate_id": candidate.candidate_id,
        "candidate_manifest_sha256": hashlib.sha256(candidate.manifest_path.read_bytes()).hexdigest(),
        "commands": commands,
        "commands_sha256": hashlib.sha256(_canonical(commands)).hexdigest(),
        "execution_performed": False,
        "npm_config_files": {
            "user": {"path": str(user_config), "sha256": empty_sha},
            "global": {"path": str(global_config), "sha256": empty_sha},
        },
        "replay_manifest_sha256": manifest["replay"]["sha256"],
        "runtime": {"npm": {"version": "11.17.0", "sha256": "a" * 64}},
        "source_commit_sha": manifest["source"]["commit_sha"],
        "source_tree_sha256": manifest["source"]["tree_sha256"],
        "status": "APPROVAL_REQUIRED",
    }
    plan["approval_input_sha256"] = hashlib.sha256(_canonical(plan)).hexdigest()
    path = candidate.path / "dependency-approval-input.json"
    path.write_bytes(json.dumps(plan, sort_keys=True, indent=2).encode() + b"\n")
    return path, plan


def _sealing_gates(candidate, plan: Path, approval: Path, install: Path):
    manifest = json.loads(candidate.manifest_path.read_text())
    overlay = candidate.path / "fixture-overlay"
    overlay.mkdir()
    (overlay / "extension.txt").write_text("declared fixture overlay\n")
    composed = compose_source(
        candidate.source_path,
        overlay,
        candidate.path / "fixture-composed",
        OverlayManifest(
            manifest["release_selection"]["target"]["tag"],
            manifest["source"]["commit_sha"],
            (("extension.txt", "ik_fixture_extension.txt"),),
        ),
    )
    runtime = candidate.path / "fixture-runtime"
    runtime.mkdir()
    (runtime / "python").write_text("pinned fixture runtime\n")
    bundle = build_release_bundle(
        ReleaseBundleInputs(
            candidate.candidate_id,
            manifest["release_selection"]["target"]["tag"],
            manifest["source"]["commit_sha"],
            composed.root,
            (ArtifactBinding("runtime", runtime),),
        ),
        candidate.layout.releases,
    )
    return GateSet(
        static_scan_clear=True,
        source_identity_clear=True,
        tests_clear=True,
        hooks_reviewed=True,
        dependency_install_clear=True,
        dependency_execution_plan=plan,
        dependency_approval_receipt=approval,
        dependency_install_receipt=install,
        release_bundle_manifest=bundle.manifest_path,
    )


def test_declared_replay_manifest_is_complete_unique_and_ordered() -> None:
    manifest = load_replay_manifest(DEFAULT_REPLAY_MANIFEST)

    assert [entry.order for entry in manifest.entries] == list(range(1, 13))
    assert [entry.commit[:8] for entry in manifest.entries] == [
        "9ce39545",
        "b030111f",
        "0bfd5b30",
        "40a21462",
        "08ba06b0",
        "d7a1229d",
        "b1846187",
        "889a3507",
        "8606fae6",
        "a9246834",
        "18a41579",
        "dca28dd0",
    ]
    assert len({entry.commit for entry in manifest.entries}) == 12


def test_candidate_build_is_idempotent_and_does_not_mutate_source(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    before_head = _git(source, "rev-parse", "HEAD")
    before_status = _git(source, "status", "--porcelain")

    first = build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)
    manifest_before = first.manifest_path.read_bytes()
    second = build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)

    assert first.path == second.path
    assert second.manifest_path.read_bytes() == manifest_before
    assert _git(source, "rev-parse", "HEAD") == before_head
    assert _git(source, "status", "--porcelain") == before_status


def test_prepared_candidate_source_snapshot_is_read_only(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)

    candidate = build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)

    verify_tree_read_only(candidate.source_path)


def test_existing_candidate_tamper_is_detected(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    selection = _selection(target_sha)
    cell = _cell("ernie")
    candidate = build_candidate(selection, cell, tmp_path / "platform", source=source)
    artifact = candidate.source_path / "README.md"
    artifact.chmod(0o644)
    artifact.write_text("tampered\n", encoding="utf-8")

    with pytest.raises(LifecycleBlockedError) as error:
        build_candidate(selection, cell, tmp_path / "platform", source=source)

    assert error.value.code == "candidate_tree_mismatch"


def test_source_head_must_match_canonical_target_and_failure_is_retained(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    (source / "README.md").write_text("moved\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-m", "move head")

    with pytest.raises(LifecycleBlockedError) as error:
        build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)

    assert error.value.code == "source_head_mismatch"
    failed = list((tmp_path / "platform" / "cells" / "ernie" / "candidates").glob("*/build-manifest.json"))
    assert len(failed) == 1
    assert json.loads(failed[0].read_text())["status"] == "FAILED"


def test_case_colliding_tracked_paths_fail_with_specific_retained_evidence(tmp_path: Path) -> None:
    source, _ = _source_repo(tmp_path, {"contributors/emails/agent@example": "first\n"})
    blob = subprocess.run(
        ["git", "-C", str(source), "hash-object", "-w", "--stdin"],
        input="second\n",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    _git(
        source,
        "update-index",
        "--add",
        "--cacheinfo",
        "100644",
        blob,
        "contributors/emails/Agent@example",
    )
    _git(source, "commit", "-m", "add case collision")
    target_sha = _git(source, "rev-parse", "HEAD")

    with pytest.raises(LifecycleBlockedError) as error:
        build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)

    assert error.value.code == "source_case_collision"
    failed = list((tmp_path / "platform" / "cells" / "ernie" / "candidates").glob("*/build-manifest.json"))
    assert len(failed) == 1
    manifest = json.loads(failed[0].read_text())
    assert manifest["status"] == "FAILED"
    assert manifest["failure"]["paths"] == [
        "contributors/emails/Agent@example",
        "contributors/emails/agent@example",
    ]


def test_case_distinct_tracked_paths_build_when_filesystem_preserves_both(tmp_path: Path) -> None:
    probe_upper = tmp_path / "CaseProbe"
    probe_lower = tmp_path / "caseprobe"
    probe_upper.write_text("upper\n", encoding="utf-8")
    probe_lower.write_text("lower\n", encoding="utf-8")
    if probe_upper.read_text(encoding="utf-8") == probe_lower.read_text(encoding="utf-8"):
        pytest.skip("test requires a case-sensitive filesystem")
    probe_upper.unlink()
    probe_lower.unlink()

    source, target_sha = _source_repo(
        tmp_path,
        {
            "contributors/emails/agent@example": "first\n",
            "contributors/emails/Agent@example": "second\n",
        },
    )

    candidate = build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)

    assert (candidate.source_path / "contributors/emails/agent@example").read_text() == "first\n"
    assert (candidate.source_path / "contributors/emails/Agent@example").read_text() == "second\n"
    verify_tree_read_only(candidate.source_path)


def test_running_source_path_is_rejected_without_mutation(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    sentinel = source / "sentinel"
    sentinel.write_text("running", encoding="utf-8")

    with pytest.raises(LifecycleBlockedError) as error:
        build_candidate(
            _selection(target_sha),
            _cell("ernie", protected_paths=(source,)),
            tmp_path / "platform",
            source=source,
        )

    assert error.value.code == "running_path_overlap"
    assert sentinel.read_text(encoding="utf-8") == "running"
    assert not (tmp_path / "platform").exists()


def test_candidate_platform_cannot_be_created_inside_source_checkout(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    platform = source / "lifecycle-artifacts"

    with pytest.raises(LifecycleBlockedError) as error:
        build_candidate(_selection(target_sha), _cell("ernie"), platform, source=source)

    assert error.value.code == "source_candidate_overlap"
    assert not platform.exists()
    assert _git(source, "status", "--porcelain") == ""


def test_ernie_and_bert_keep_separate_target_and_candidate_records(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    platform = tmp_path / "platform"

    ernie = build_candidate(_selection(target_sha), _cell("ernie"), platform, source=source)
    bert = build_candidate(_selection(target_sha), _cell("bert"), platform, source=source)

    assert ernie.path != bert.path
    ernie_target = json.loads((ernie.layout.state / "target.json").read_text())
    bert_target = json.loads((bert.layout.state / "target.json").read_text())
    assert ernie_target["cell_id"] == "ernie"
    assert bert_target["cell_id"] == "bert"
    assert ernie_target["target"] == bert_target["target"]


def test_candidate_manifest_records_provenance_replay_and_promotion_gates(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path, {"uv.lock": "version = 1\n", "README.md": "fixture\n"})

    candidate = build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)
    manifest = json.loads(candidate.manifest_path.read_text())

    assert manifest["schema_id"] == "ik.hermes.candidate-build-manifest.v1"
    assert manifest["cell"]["id"] == "ernie"
    assert manifest["source"]["commit_sha"] == target_sha
    assert len(manifest["source"]["tree_sha256"]) == 64
    assert manifest["replay"]["entry_count"] == 12
    assert len(manifest["replay"]["sha256"]) == 64
    assert manifest["dependency_phase"]["status"] == "approval_required"
    assert "legacy_health_automation_pause" in manifest["promotion_blockers"]
    assert manifest["external_approval_gates"]["computer_history_path_adaptation"] == "approval_required"


def test_forbidden_candidate_is_retained_as_failed(tmp_path: Path) -> None:
    source, target_sha = _source_repo(
        tmp_path,
        {"package.json": '{"dependencies":{"axios":"1.14.1"}}'},
    )

    with pytest.raises(LifecycleBlockedError) as error:
        build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)

    assert error.value.code == "forbidden_dependency"
    manifests = list((tmp_path / "platform" / "cells" / "ernie" / "candidates").glob("*/build-manifest.json"))
    assert len(manifests) == 1
    failed = json.loads(manifests[0].read_text())
    assert failed["status"] == "FAILED"
    assert failed["failure"]["code"] == "forbidden_dependency"
    assert (manifests[0].parent / "source" / "package.json").exists()


def test_upstream_only_candidate_cannot_be_sealed(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    candidate = build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)
    gates = GateSet(
        static_scan_clear=True,
        source_identity_clear=True,
        tests_clear=True,
        hooks_reviewed=True,
        dependency_install_clear=True,
    )

    with pytest.raises(LifecycleBlockedError) as error:
        seal_candidate(candidate, gates)

    assert error.value.code == "composed_release_bundle_required"
    assert not any(candidate.layout.releases.iterdir())


def test_build_gate_boolean_cannot_replace_dependency_approval_receipt(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    candidate = build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)
    plan_path, _ = _dependency_plan(candidate)
    gates = _sealing_gates(candidate, plan_path, None, None)

    with pytest.raises(LifecycleBlockedError) as error:
        seal_candidate(candidate, gates)

    assert error.value.code == "dependency_approval_required"
    assert json.loads(candidate.manifest_path.read_text())["status"] == "STATIC_PREPARED"


def test_relative_static_plan_digest_cannot_authorize_dependency_sealing(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    candidate = build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)
    plan_path, _ = _dependency_plan(candidate)
    manifest = json.loads(candidate.manifest_path.read_text())
    approval_path = tmp_path / "approval.json"
    approval_sha = _write_evidence(
        approval_path,
        "dependency_execution_approval",
        "APPROVED",
        {
            "candidate_id": candidate.candidate_id,
            "candidate_tree_sha256": manifest["source"]["tree_sha256"],
            "planned_commands_sha256": hashlib.sha256(
                _canonical(manifest["dependency_phase"]["planned_commands"])
            ).hexdigest(),
        },
    )
    install_path = tmp_path / "install.json"
    _write_evidence(
        install_path,
        "dependency_install_result",
        "CLEAR",
        {
            "candidate_id": candidate.candidate_id,
            "candidate_tree_sha256": manifest["source"]["tree_sha256"],
            "approval_file_sha256": approval_sha,
        },
    )

    with pytest.raises(LifecycleBlockedError) as error:
        seal_candidate(candidate, _sealing_gates(candidate, plan_path, approval_path, install_path))

    assert error.value.code == "approval_receipt_mismatch"
    assert json.loads(candidate.manifest_path.read_text())["status"] == "STATIC_PREPARED"


def test_sealing_binds_approval_and_install_to_derived_execution_plan(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    candidate = build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)
    plan_path, plan = _dependency_plan(candidate)
    manifest = json.loads(candidate.manifest_path.read_text())
    binding = {
        "candidate_id": candidate.candidate_id,
        "candidate_tree_sha256": manifest["source"]["tree_sha256"],
        "execution_plan_sha256": plan["approval_input_sha256"],
        "commands_sha256": plan["commands_sha256"],
    }
    approval_path = tmp_path / "approval.json"
    approval_sha = _write_evidence(
        approval_path,
        "dependency_execution_approval",
        "APPROVED",
        binding,
    )
    install_path = tmp_path / "install.json"
    _write_evidence(
        install_path,
        "dependency_install_result",
        "CLEAR",
        {**binding, "approval_file_sha256": approval_sha},
    )

    release = seal_candidate(candidate, _sealing_gates(candidate, plan_path, approval_path, install_path))

    assert release.is_dir()
    verify_tree_read_only(release)


def test_dependency_execution_plan_rejects_shared_npm_config_path(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    candidate = build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)
    plan_path, plan = _dependency_plan(candidate, shared_npm_config=True)
    binding = {
        "candidate_id": candidate.candidate_id,
        "candidate_tree_sha256": plan["source_tree_sha256"],
        "execution_plan_sha256": plan["approval_input_sha256"],
        "commands_sha256": plan["commands_sha256"],
    }
    approval_path = tmp_path / "approval.json"
    approval_sha = _write_evidence(
        approval_path,
        "dependency_execution_approval",
        "APPROVED",
        binding,
    )
    install_path = tmp_path / "install.json"
    _write_evidence(
        install_path,
        "dependency_install_result",
        "CLEAR",
        {**binding, "approval_file_sha256": approval_sha},
    )

    with pytest.raises(LifecycleBlockedError) as error:
        seal_candidate(candidate, _sealing_gates(candidate, plan_path, approval_path, install_path))

    assert error.value.code == "dependency_execution_plan_invalid"


def test_tampered_dependency_execution_plan_fails_closed(tmp_path: Path) -> None:
    source, target_sha = _source_repo(tmp_path)
    candidate = build_candidate(_selection(target_sha), _cell("ernie"), tmp_path / "platform", source=source)
    plan_path, plan = _dependency_plan(candidate)
    plan["commands"][0]["argv"].append("--foreground-scripts")
    plan_path.write_bytes(json.dumps(plan, sort_keys=True, indent=2).encode() + b"\n")
    approval_path = tmp_path / "approval.json"
    approval_sha = _write_evidence(
        approval_path,
        "dependency_execution_approval",
        "APPROVED",
        {
            "candidate_id": candidate.candidate_id,
            "candidate_tree_sha256": plan["source_tree_sha256"],
            "execution_plan_sha256": plan["approval_input_sha256"],
            "commands_sha256": plan["commands_sha256"],
        },
    )
    install_path = tmp_path / "install.json"
    _write_evidence(
        install_path,
        "dependency_install_result",
        "CLEAR",
        {
            "candidate_id": candidate.candidate_id,
            "candidate_tree_sha256": plan["source_tree_sha256"],
            "execution_plan_sha256": plan["approval_input_sha256"],
            "commands_sha256": plan["commands_sha256"],
            "approval_file_sha256": approval_sha,
        },
    )

    with pytest.raises(LifecycleBlockedError) as error:
        seal_candidate(candidate, _sealing_gates(candidate, plan_path, approval_path, install_path))

    assert error.value.code == "dependency_execution_plan_invalid"
