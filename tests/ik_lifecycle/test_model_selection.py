from __future__ import annotations

import copy
import hashlib
import json

import pytest

from ik_lifecycle.model_selection import (
    ModelSelectionError,
    validate_closed_runtime_plan,
    validate_selection_receipt,
)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _selection() -> dict[str, object]:
    body = {
        "kind": "model_candidate_selection",
        "schema_version": "1.0",
        "status": "SELECTED_CANDIDATE_NOT_DEPLOYED",
        "data": {
            "cell": "ernie",
            "candidate_role": "primary_agentic_generalist_candidate",
            "artifacts": [
                {"role": "model", "bytes": 18_973_870_432, "sha256": "1" * 64, "mode": "0400"},
                {"role": "projector", "bytes": 629_247_008, "sha256": "2" * 64, "mode": "0400"},
            ],
            "import_manifest_sha256": "3" * 64,
            "release": {
                "composition_manifest_sha256": "4" * 64,
                "composed_tree_sha256": "5" * 64,
                "bundle_manifest_sha256": "6" * 64,
                "status": "SEALED_CODE_ONLY",
                "read_only": True,
            },
            "approval_contract": {
                "api_sha256": "7" * 64,
                "schema_sha256": "8" * 64,
                "states": ["required", "approved", "denied", "not_required"],
                "fail_closed": True,
                "model_neutral": True,
            },
            "evaluation": {
                "receipt_sha256": "9" * 64,
                "concurrency_receipt_sha256": "a" * 64,
                "passed": 12,
                "total": 12,
                "failed": 0,
                "errors": 0,
                "timeouts": 0,
                "requested_concurrency": 2,
                "successful_concurrency": 2,
                "unchanged_strict_suite": True,
            },
            "resources": {
                "unified_memory_bytes": 68_719_476_736,
                "maximum_runtime_rss_bytes": 55_834_574_848,
                "context_tokens": 32_768,
                "maximum_concurrency": 2,
            },
            "prerequisites": {
                "canary": "CLEAR",
                "immutable_backup": "CLEAR",
                "rp2": "CLEAR",
                "rp3_crash_recovery": "CLEAR",
                "rp3_pretraffic": "CLEAR",
                "legacy_health_automation": "BLOCKER_ACTIVE_UNTIL_APPROVAL_PAUSED",
                "fresh_final_backup": "REQUIRED_BEFORE_LIVE_PROMOTION",
                "final_delta": "REQUIRED_BEFORE_LIVE_PROMOTION",
                "bert_phase": "SEPARATE_LATER_APPROVAL",
            },
            "selection_scope": {
                "candidate_only": True,
                "live_configuration_changed": False,
                "service_or_pointer_changed": False,
                "promotion_authorized": False,
            },
        },
    }
    return {"receipt": body, "sha256": _digest(body)}


def _plan(selection_sha256: str) -> dict[str, object]:
    body = {
        "schema_id": "ik.hermes.credential-bound-closed-runtime-plan.v1",
        "status": "PREPARED_NOT_EXECUTABLE",
        "cell": "ernie",
        "selection_sha256": selection_sha256,
        "bindings": {
            "model_sha256": "1" * 64,
            "projector_sha256": "2" * 64,
            "import_manifest_sha256": "3" * 64,
            "composition_manifest_sha256": "4" * 64,
            "composed_tree_sha256": "5" * 64,
            "bundle_manifest_sha256": "6" * 64,
            "approval_api_sha256": "7" * 64,
            "approval_schema_sha256": "8" * 64,
            "evaluation_receipt_sha256": "9" * 64,
            "concurrency_receipt_sha256": "a" * 64,
            "runtime_binary_sha256": "b" * 64,
        },
        "execution": {"authorized": False, "performed": False, "requires_new_authority": True},
        "opaque_handle_classes": [
            {"class": "ernie_profile_secret_bundle", "resolved": False},
            {"class": "nate_os_local_agent_identity", "resolved": False},
        ],
        "data_policy": {
            "fixture_class": "public_synthetic_only",
            "private_content_allowed": False,
            "private_clone_access": False,
            "cloud_or_bert_exposure": False,
        },
        "network": {
            "policy": "macos-sandbox-exec-loopback-only",
            "outbound": "deny",
            "listen": "loopback-only",
            "fresh_proof_max_age_seconds": 300,
        },
        "services": {
            "isolated_cell_only": True,
            "production_traffic": False,
            "service_manager_actions": False,
        },
        "resource_limits": {
            "unified_memory_bytes": 68_719_476_736,
            "maximum_runtime_rss_bytes": 55_834_574_848,
            "minimum_free_storage_bytes": 107_374_182_400,
            "context_tokens": 32_768,
            "maximum_concurrency": 2,
        },
        "ordered_operations": [
            {"id": "rebind-immutable-inputs", "executed": False},
            {"id": "resolve-opaque-handles", "executed": False},
            {"id": "fresh-network-proof", "executed": False},
            {"id": "start-loopback-model-worker", "executed": False},
            {"id": "start-isolated-ernie-cell", "executed": False},
            {"id": "run-public-synthetic-gates", "executed": False},
            {"id": "verify-zero-private-exposure", "executed": False},
            {"id": "stop-isolated-cell", "executed": False},
            {"id": "rehearse-rp2-rp3-rollback", "executed": False},
        ],
        "stop_conditions": [
            "binding-drift",
            "forbidden-dependency-evidence",
            "opaque-handle-unavailable-or-ambiguous",
            "private-content-requested",
            "network-denial-failure",
            "unexpected-external-or-service-effect",
            "resource-ceiling-exceeded",
            "health-or-test-failure",
            "rollback-uncertainty",
            "automation-overlap",
        ],
        "automation": {
            "mutation_authorized": False,
            "legacy_health_automation": "FINAL_PROMOTION_BLOCKER_UNTIL_APPROVAL_PAUSED",
            "replacement_activation": "NOT_AUTHORIZED",
            "computer_history_path_adaptation": "SEPARATE_APPROVAL_GATE",
        },
        "backup_and_delta": {
            "immutable_rollback_pair_required": True,
            "fresh_final_backup_required_before_live": True,
            "final_delta_required_before_live": True,
            "live_profile_access": False,
        },
        "bert": {"included": False, "phase": "SEPARATE_LATER_APPROVAL"},
    }
    return {"plan": body, "sha256": _digest(body)}


def _clone_bound_plan(selection_sha256: str) -> dict[str, object]:
    document = _plan(selection_sha256)
    plan = document["plan"]
    plan["schema_id"] = "ik.hermes.credential-bound-closed-runtime-plan.v2"
    plan["bindings"].update(
        {
            "shared_bundle_receipt_sha256": "c" * 64,
            "semantic_receipt_sha256": "d" * 64,
            "migrated_tree_sha256": "e" * 64,
            "rollback_artifact_sha256": "f" * 64,
        }
    )
    plan["data_policy"] = {
        "fixture_class": "public_synthetic_with_aggregate_bound_migration_clone",
        "private_content_allowed": "isolated_runtime_startup_only",
        "private_clone_access": "read_only_aggregate_bound",
        "model_exposure": False,
        "cloud_or_bert_exposure": False,
    }
    plan["ordered_operations"].insert(2, {"id": "bind-migration-clone", "executed": False})
    document["sha256"] = _digest(plan)
    return document


def test_selection_is_machine_verifiable_and_candidate_only() -> None:
    selection = _selection()
    expected = {
        "import_manifest_sha256": "3" * 64,
        "composition_manifest_sha256": "4" * 64,
        "evaluation_receipt_sha256": "9" * 64,
    }
    assert validate_selection_receipt(selection, expected_bindings=expected) == selection["sha256"]


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        (("receipt", "status"), "SELECTED_LIVE", "selection_status_invalid"),
        (("receipt", "data", "evaluation", "passed"), 11, "selection_eval_ineligible"),
        (("receipt", "data", "approval_contract", "states"), ["approved"], "selection_approval_contract_invalid"),
        (("receipt", "data", "resources", "maximum_runtime_rss_bytes"), 70_000_000_000, "selection_resources_invalid"),
        (("receipt", "data", "prerequisites", "rp3_pretraffic"), "BLOCKED", "selection_prerequisite_invalid"),
        (("receipt", "data", "selection_scope", "live_configuration_changed"), True, "selection_scope_invalid"),
    ],
)
def test_selection_fails_closed_on_ineligible_evidence(path: tuple[str, ...], value: object, code: str) -> None:
    selection = _selection()
    current = selection
    for key in path[:-1]:
        current = current[key]  # type: ignore[index,assignment]
    current[path[-1]] = value  # type: ignore[index]
    selection["sha256"] = _digest(selection["receipt"])
    with pytest.raises(ModelSelectionError, match=code):
        validate_selection_receipt(selection)


def test_selection_rejects_digest_and_observed_binding_drift() -> None:
    selection = _selection()
    selection["sha256"] = "0" * 64
    with pytest.raises(ModelSelectionError, match="selection_digest_mismatch"):
        validate_selection_receipt(selection)
    selection = _selection()
    with pytest.raises(ModelSelectionError, match="selection_binding_mismatch"):
        validate_selection_receipt(selection, expected_bindings={"import_manifest_sha256": "f" * 64})


def test_closed_runtime_plan_is_non_executable_private_safe_and_separate_from_bert() -> None:
    selection = _selection()
    plan = _plan(selection["sha256"])
    assert validate_closed_runtime_plan(plan, selection_sha256=selection["sha256"]) == plan["sha256"]


def test_v2_closed_runtime_plan_allows_only_aggregate_bound_clone_startup_and_denies_model_exposure() -> None:
    selection = _selection()
    plan = _clone_bound_plan(selection["sha256"])

    assert validate_closed_runtime_plan(plan, selection_sha256=selection["sha256"]) == plan["sha256"]

    exposed = copy.deepcopy(plan)
    exposed["plan"]["data_policy"]["model_exposure"] = True
    exposed["sha256"] = _digest(exposed["plan"])
    with pytest.raises(ModelSelectionError, match="closed_plan_privacy_invalid"):
        validate_closed_runtime_plan(exposed, selection_sha256=selection["sha256"])

    missing_clone_binding = copy.deepcopy(plan)
    del missing_clone_binding["plan"]["bindings"]["migrated_tree_sha256"]
    missing_clone_binding["sha256"] = _digest(missing_clone_binding["plan"])
    with pytest.raises(ModelSelectionError, match="closed_plan_binding_invalid"):
        validate_closed_runtime_plan(missing_clone_binding, selection_sha256=selection["sha256"])


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda plan: plan["plan"]["execution"].__setitem__("authorized", True), "closed_plan_execution_invalid"),
        (lambda plan: plan["plan"]["opaque_handle_classes"][0].__setitem__("resolved", True), "closed_plan_handle_invalid"),
        (lambda plan: plan["plan"]["data_policy"].__setitem__("private_content_allowed", True), "closed_plan_privacy_invalid"),
        (lambda plan: plan["plan"]["network"].__setitem__("outbound", "allow"), "closed_plan_network_invalid"),
        (lambda plan: plan["plan"]["automation"].__setitem__("legacy_health_automation", "ACTIVE_OK"), "closed_plan_automation_invalid"),
        (lambda plan: plan["plan"]["backup_and_delta"].__setitem__("final_delta_required_before_live", False), "closed_plan_rollback_invalid"),
        (lambda plan: plan["plan"]["bert"].__setitem__("included", True), "closed_plan_bert_boundary_invalid"),
        (lambda plan: plan["plan"]["ordered_operations"].pop(), "closed_plan_operations_invalid"),
    ],
)
def test_closed_runtime_plan_mutations_fail_closed(mutator, code: str) -> None:
    plan = _plan(_selection()["sha256"])
    mutator(plan)
    plan["sha256"] = _digest(plan["plan"])
    with pytest.raises(ModelSelectionError, match=code):
        validate_closed_runtime_plan(plan, selection_sha256=_selection()["sha256"])


def test_closed_runtime_plan_rejects_selection_and_plan_digest_drift() -> None:
    selection_sha256 = _selection()["sha256"]
    plan = _plan(selection_sha256)
    with pytest.raises(ModelSelectionError, match="closed_plan_selection_mismatch"):
        validate_closed_runtime_plan(plan, selection_sha256="f" * 64)
    plan["sha256"] = "0" * 64
    with pytest.raises(ModelSelectionError, match="closed_plan_digest_mismatch"):
        validate_closed_runtime_plan(plan, selection_sha256=selection_sha256)


def test_closed_runtime_plan_rejects_private_payload_fields() -> None:
    plan = _plan(_selection()["sha256"])
    plan["plan"]["private_prompt"] = "must never be present"  # type: ignore[index]
    plan["sha256"] = _digest(plan["plan"])
    with pytest.raises(ModelSelectionError, match="closed_plan_privacy_invalid"):
        validate_closed_runtime_plan(plan, selection_sha256=_selection()["sha256"])


def test_closed_runtime_plan_rejects_resource_and_binding_drift() -> None:
    plan = _plan(_selection()["sha256"])
    plan["plan"]["resource_limits"] = {  # type: ignore[index]
        "unified_memory_bytes": 68_719_476_736,
        "maximum_runtime_rss_bytes": 70_000_000_000,
        "minimum_free_storage_bytes": 107_374_182_400,
        "context_tokens": 32_768,
        "maximum_concurrency": 2,
    }
    plan["sha256"] = _digest(plan["plan"])
    with pytest.raises(ModelSelectionError, match="closed_plan_resources_invalid"):
        validate_closed_runtime_plan(
            plan,
            selection_sha256=_selection()["sha256"],
            expected_bindings={"model_sha256": "1" * 64},
        )

    plan["plan"]["resource_limits"]["maximum_runtime_rss_bytes"] = 55_834_574_848  # type: ignore[index]
    plan["plan"]["bindings"]["model_sha256"] = "e" * 64  # type: ignore[index]
    plan["sha256"] = _digest(plan["plan"])
    with pytest.raises(ModelSelectionError, match="closed_plan_binding_mismatch"):
        validate_closed_runtime_plan(
            plan,
            selection_sha256=_selection()["sha256"],
            expected_bindings={"model_sha256": "f" * 64},
        )
