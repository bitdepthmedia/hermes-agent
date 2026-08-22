"""Fail-closed candidate selection and closed-runtime preparation contracts."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping, Sequence


_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_APPROVAL_STATES = ("required", "approved", "denied", "not_required")
_CLOSED_OPERATIONS = (
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
_CLOSED_STOP_CONDITIONS = frozenset(
    {
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
    }
)
_CLOSED_BINDINGS = frozenset(
    {
        "model_sha256",
        "projector_sha256",
        "import_manifest_sha256",
        "composition_manifest_sha256",
        "composed_tree_sha256",
        "bundle_manifest_sha256",
        "approval_api_sha256",
        "approval_schema_sha256",
        "evaluation_receipt_sha256",
        "concurrency_receipt_sha256",
        "runtime_binary_sha256",
    }
)
_PRIVATE_PAYLOAD_KEYS = frozenset(
    {
        "api_key",
        "credential_value",
        "password",
        "path",
        "private_content",
        "private_prompt",
        "raw",
        "raw_value",
        "secret_value",
        "token",
        "value",
    }
)


class ModelSelectionError(RuntimeError):
    """A non-sensitive selection or preparation validation error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ModelSelectionError(code)
    return value


def _hex(value: object) -> bool:
    return isinstance(value, str) and _HEX64.fullmatch(value) is not None


def _has_private_payload(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _PRIVATE_PAYLOAD_KEYS or _has_private_payload(child):
                return True
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_has_private_payload(child) for child in value)
    if isinstance(value, str):
        lowered = value.lower()
        return lowered.startswith("/users/") or lowered.startswith("/home/") or "\\users\\" in lowered
    return False


def _artifact_by_role(data: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, Sequence) or isinstance(artifacts, (str, bytes)):
        raise ModelSelectionError("selection_artifact_invalid")
    by_role: dict[str, Mapping[str, object]] = {}
    for raw in artifacts:
        artifact = _mapping(raw, "selection_artifact_invalid")
        role = artifact.get("role")
        if (
            role not in {"model", "projector"}
            or role in by_role
            or not isinstance(artifact.get("bytes"), int)
            or int(artifact["bytes"]) <= 0
            or not _hex(artifact.get("sha256"))
            or artifact.get("mode") != "0400"
        ):
            raise ModelSelectionError("selection_artifact_invalid")
        by_role[str(role)] = artifact
    if set(by_role) != {"model", "projector"}:
        raise ModelSelectionError("selection_artifact_invalid")
    return by_role


def _binding_value(data: Mapping[str, object], name: str, artifacts: Mapping[str, Mapping[str, object]]) -> object:
    release = _mapping(data.get("release"), "selection_release_invalid")
    approval = _mapping(data.get("approval_contract"), "selection_approval_contract_invalid")
    evaluation = _mapping(data.get("evaluation"), "selection_eval_ineligible")
    paths: dict[str, object] = {
        "model_sha256": artifacts["model"].get("sha256"),
        "projector_sha256": artifacts["projector"].get("sha256"),
        "import_manifest_sha256": data.get("import_manifest_sha256"),
        "composition_manifest_sha256": release.get("composition_manifest_sha256"),
        "composed_tree_sha256": release.get("composed_tree_sha256"),
        "bundle_manifest_sha256": release.get("bundle_manifest_sha256"),
        "approval_api_sha256": approval.get("api_sha256"),
        "approval_schema_sha256": approval.get("schema_sha256"),
        "evaluation_receipt_sha256": evaluation.get("receipt_sha256"),
        "concurrency_receipt_sha256": evaluation.get("concurrency_receipt_sha256"),
    }
    if name not in paths:
        raise ModelSelectionError("selection_binding_unknown")
    return paths[name]


def validate_selection_receipt(
    document: Mapping[str, object], *, expected_bindings: Mapping[str, str] | None = None
) -> str:
    """Validate an exact Ernie candidate selection without granting deployment."""

    envelope = _mapping(document, "selection_document_invalid")
    receipt = _mapping(envelope.get("receipt"), "selection_document_invalid")
    digest = envelope.get("sha256")
    if not _hex(digest) or digest != canonical_sha256(receipt):
        raise ModelSelectionError("selection_digest_mismatch")
    if (
        receipt.get("kind") != "model_candidate_selection"
        or receipt.get("schema_version") != "1.0"
        or receipt.get("status") != "SELECTED_CANDIDATE_NOT_DEPLOYED"
    ):
        raise ModelSelectionError("selection_status_invalid")
    data = _mapping(receipt.get("data"), "selection_document_invalid")
    if data.get("cell") != "ernie" or data.get("candidate_role") != "primary_agentic_generalist_candidate":
        raise ModelSelectionError("selection_status_invalid")
    artifacts = _artifact_by_role(data)
    if not _hex(data.get("import_manifest_sha256")):
        raise ModelSelectionError("selection_artifact_invalid")

    release = _mapping(data.get("release"), "selection_release_invalid")
    if (
        release.get("status") != "SEALED_CODE_ONLY"
        or release.get("read_only") is not True
        or any(not _hex(release.get(name)) for name in (
            "composition_manifest_sha256", "composed_tree_sha256", "bundle_manifest_sha256"
        ))
    ):
        raise ModelSelectionError("selection_release_invalid")

    approval = _mapping(data.get("approval_contract"), "selection_approval_contract_invalid")
    if (
        tuple(approval.get("states", ())) != _APPROVAL_STATES
        or approval.get("fail_closed") is not True
        or approval.get("model_neutral") is not True
        or not _hex(approval.get("api_sha256"))
        or not _hex(approval.get("schema_sha256"))
    ):
        raise ModelSelectionError("selection_approval_contract_invalid")

    evaluation = _mapping(data.get("evaluation"), "selection_eval_ineligible")
    if (
        evaluation.get("passed") != 12
        or evaluation.get("total") != 12
        or evaluation.get("failed") != 0
        or evaluation.get("errors") != 0
        or evaluation.get("timeouts") != 0
        or evaluation.get("requested_concurrency") != 2
        or evaluation.get("successful_concurrency") != 2
        or evaluation.get("unchanged_strict_suite") is not True
        or not _hex(evaluation.get("receipt_sha256"))
        or not _hex(evaluation.get("concurrency_receipt_sha256"))
    ):
        raise ModelSelectionError("selection_eval_ineligible")

    resources = _mapping(data.get("resources"), "selection_resources_invalid")
    unified = resources.get("unified_memory_bytes")
    maximum_rss = resources.get("maximum_runtime_rss_bytes")
    if (
        not isinstance(unified, int)
        or not isinstance(maximum_rss, int)
        or unified <= 0
        or maximum_rss <= 0
        or maximum_rss > unified
        or not isinstance(resources.get("context_tokens"), int)
        or int(resources["context_tokens"]) < 32_768
        or not isinstance(resources.get("maximum_concurrency"), int)
        or int(resources["maximum_concurrency"]) < 2
    ):
        raise ModelSelectionError("selection_resources_invalid")

    prerequisites = _mapping(data.get("prerequisites"), "selection_prerequisite_invalid")
    required = {
        "canary": "CLEAR",
        "immutable_backup": "CLEAR",
        "rp2": "CLEAR",
        "rp3_crash_recovery": "CLEAR",
        "rp3_pretraffic": "CLEAR",
        "legacy_health_automation": "BLOCKER_ACTIVE_UNTIL_APPROVAL_PAUSED",
        "fresh_final_backup": "REQUIRED_BEFORE_LIVE_PROMOTION",
        "final_delta": "REQUIRED_BEFORE_LIVE_PROMOTION",
        "bert_phase": "SEPARATE_LATER_APPROVAL",
    }
    if any(prerequisites.get(key) != value for key, value in required.items()):
        raise ModelSelectionError("selection_prerequisite_invalid")

    scope = _mapping(data.get("selection_scope"), "selection_scope_invalid")
    if scope != {
        "candidate_only": True,
        "live_configuration_changed": False,
        "service_or_pointer_changed": False,
        "promotion_authorized": False,
    }:
        raise ModelSelectionError("selection_scope_invalid")

    for name, expected in (expected_bindings or {}).items():
        if _binding_value(data, name, artifacts) != expected:
            raise ModelSelectionError("selection_binding_mismatch")
    return str(digest)


def validate_closed_runtime_plan(
    document: Mapping[str, object],
    *,
    selection_sha256: str,
    expected_bindings: Mapping[str, str] | None = None,
) -> str:
    """Validate a credential-bound plan that deliberately cannot execute yet."""

    envelope = _mapping(document, "closed_plan_document_invalid")
    plan = _mapping(envelope.get("plan"), "closed_plan_document_invalid")
    digest = envelope.get("sha256")
    if not _hex(digest) or digest != canonical_sha256(plan):
        raise ModelSelectionError("closed_plan_digest_mismatch")
    if not _hex(selection_sha256) or plan.get("selection_sha256") != selection_sha256:
        raise ModelSelectionError("closed_plan_selection_mismatch")
    if (
        plan.get("schema_id") != "ik.hermes.credential-bound-closed-runtime-plan.v1"
        or plan.get("status") != "PREPARED_NOT_EXECUTABLE"
        or plan.get("cell") != "ernie"
    ):
        raise ModelSelectionError("closed_plan_status_invalid")
    if _has_private_payload(plan):
        raise ModelSelectionError("closed_plan_privacy_invalid")

    bindings = _mapping(plan.get("bindings"), "closed_plan_binding_invalid")
    if set(bindings) != _CLOSED_BINDINGS or any(not _hex(value) for value in bindings.values()):
        raise ModelSelectionError("closed_plan_binding_invalid")
    for name, expected in (expected_bindings or {}).items():
        if name not in _CLOSED_BINDINGS or bindings.get(name) != expected:
            raise ModelSelectionError("closed_plan_binding_mismatch")

    execution = _mapping(plan.get("execution"), "closed_plan_execution_invalid")
    if execution != {"authorized": False, "performed": False, "requires_new_authority": True}:
        raise ModelSelectionError("closed_plan_execution_invalid")

    handles = plan.get("opaque_handle_classes")
    if not isinstance(handles, Sequence) or isinstance(handles, (str, bytes)):
        raise ModelSelectionError("closed_plan_handle_invalid")
    classes: set[str] = set()
    for raw in handles:
        handle = _mapping(raw, "closed_plan_handle_invalid")
        name = handle.get("class")
        if set(handle) != {"class", "resolved"} or not isinstance(name, str) or not name or handle.get("resolved") is not False:
            raise ModelSelectionError("closed_plan_handle_invalid")
        classes.add(name)
    if classes != {"ernie_profile_secret_bundle", "nate_os_local_agent_identity"}:
        raise ModelSelectionError("closed_plan_handle_invalid")

    policy = _mapping(plan.get("data_policy"), "closed_plan_privacy_invalid")
    if policy != {
        "fixture_class": "public_synthetic_only",
        "private_content_allowed": False,
        "private_clone_access": False,
        "cloud_or_bert_exposure": False,
    }:
        raise ModelSelectionError("closed_plan_privacy_invalid")

    network = _mapping(plan.get("network"), "closed_plan_network_invalid")
    if (
        network.get("policy") != "macos-sandbox-exec-loopback-only"
        or network.get("outbound") != "deny"
        or network.get("listen") != "loopback-only"
        or not isinstance(network.get("fresh_proof_max_age_seconds"), int)
        or not 0 < int(network["fresh_proof_max_age_seconds"]) <= 300
    ):
        raise ModelSelectionError("closed_plan_network_invalid")

    services = _mapping(plan.get("services"), "closed_plan_service_invalid")
    if services != {"isolated_cell_only": True, "production_traffic": False, "service_manager_actions": False}:
        raise ModelSelectionError("closed_plan_service_invalid")

    resources = _mapping(plan.get("resource_limits"), "closed_plan_resources_invalid")
    unified = resources.get("unified_memory_bytes")
    maximum_rss = resources.get("maximum_runtime_rss_bytes")
    minimum_storage = resources.get("minimum_free_storage_bytes")
    if (
        set(resources) != {
            "unified_memory_bytes",
            "maximum_runtime_rss_bytes",
            "minimum_free_storage_bytes",
            "context_tokens",
            "maximum_concurrency",
        }
        or not isinstance(unified, int)
        or not isinstance(maximum_rss, int)
        or not isinstance(minimum_storage, int)
        or maximum_rss <= 0
        or maximum_rss > unified
        or minimum_storage <= 0
        or resources.get("context_tokens") != 32_768
        or resources.get("maximum_concurrency") != 2
    ):
        raise ModelSelectionError("closed_plan_resources_invalid")

    operations = plan.get("ordered_operations")
    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes)):
        raise ModelSelectionError("closed_plan_operations_invalid")
    observed_ids: list[str] = []
    for raw in operations:
        operation = _mapping(raw, "closed_plan_operations_invalid")
        if operation.get("executed") is not False or not isinstance(operation.get("id"), str):
            raise ModelSelectionError("closed_plan_operations_invalid")
        observed_ids.append(str(operation["id"]))
    if tuple(observed_ids) != _CLOSED_OPERATIONS:
        raise ModelSelectionError("closed_plan_operations_invalid")

    stops = plan.get("stop_conditions")
    if not isinstance(stops, Sequence) or isinstance(stops, (str, bytes)) or frozenset(stops) != _CLOSED_STOP_CONDITIONS:
        raise ModelSelectionError("closed_plan_stop_conditions_invalid")

    automation = _mapping(plan.get("automation"), "closed_plan_automation_invalid")
    if automation != {
        "mutation_authorized": False,
        "legacy_health_automation": "FINAL_PROMOTION_BLOCKER_UNTIL_APPROVAL_PAUSED",
        "replacement_activation": "NOT_AUTHORIZED",
        "computer_history_path_adaptation": "SEPARATE_APPROVAL_GATE",
    }:
        raise ModelSelectionError("closed_plan_automation_invalid")

    rollback = _mapping(plan.get("backup_and_delta"), "closed_plan_rollback_invalid")
    if rollback != {
        "immutable_rollback_pair_required": True,
        "fresh_final_backup_required_before_live": True,
        "final_delta_required_before_live": True,
        "live_profile_access": False,
    }:
        raise ModelSelectionError("closed_plan_rollback_invalid")

    bert = _mapping(plan.get("bert"), "closed_plan_bert_boundary_invalid")
    if bert != {"included": False, "phase": "SEPARATE_LATER_APPROVAL"}:
        raise ModelSelectionError("closed_plan_bert_boundary_invalid")
    return str(digest)
