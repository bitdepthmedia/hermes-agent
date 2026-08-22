"""Machine-verifiable mapping from approved architecture invariants to tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .focused_test_selection import BEHAVIOR_TEST_PATHS
from .models import LifecycleBlockedError


REQUIRED_ARCHITECTURE_INVARIANTS = (
    "bert_ernie_peer_chief_personas",
    "codex_exactly_once_work_ownership",
    "personal_and_mixed_routing",
    "deterministic_execution_ladder",
    "ernie_private_sanitized_bert_reintegration",
    "nate_os_visibility_and_write_boundaries",
    "offline_ernie_25_minute_idempotent_retry",
    "evidence_gated_self_improvement",
    "task_boundary_model_workers_no_keyword_swap",
    "independent_ernie_bert_cells",
    "semantic_continuity_boundaries",
    "rollback_and_sealing_boundaries",
)


def _sha256(path: Path, code: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise LifecycleBlockedError(code, "architecture evidence file is unavailable") from exc


def validate_architecture_coverage(
    mapping: Mapping[str, Any],
    behavior_proof: Mapping[str, Any],
    lifecycle_proof: Mapping[str, Any],
    *,
    evidence_root: Path | None = None,
) -> dict[str, Any]:
    """Fail closed unless every approved invariant maps to an exact discovered test."""

    if mapping.get("schema_id") != "ik.hermes.architecture-coverage.v1":
        raise LifecycleBlockedError("architecture_mapping_schema_invalid", "architecture coverage schema is invalid")
    if behavior_proof.get("suite_id") != "behavior" or tuple(behavior_proof.get("selected_paths", ())) != BEHAVIOR_TEST_PATHS:
        raise LifecycleBlockedError("behavior_test_file_set_invalid", "behavior test selection is not the exact declared file set")
    if behavior_proof.get("test_count", 0) < 1 or len(behavior_proof.get("test_ids", ())) != behavior_proof.get("test_count"):
        raise LifecycleBlockedError("behavior_test_selection_empty", "behavior test discovery is empty or inconsistent")
    suites = mapping.get("suites")
    if not isinstance(suites, Mapping) or set(suites) != {"ik-orchestration", "ik-models"}:
        raise LifecycleBlockedError("architecture_suite_missing", "both behavior suites are required")
    expected_suites = {
        "ik-orchestration": [path for path in BEHAVIOR_TEST_PATHS if "/ik_orchestration/" in path],
        "ik-models": [path for path in BEHAVIOR_TEST_PATHS if "/ik_models/" in path],
    }
    for name, paths in expected_suites.items():
        if suites[name].get("required_paths") != paths:
            raise LifecycleBlockedError("behavior_test_file_set_invalid", f"{name} test paths changed")
    invariants = mapping.get("invariants")
    if not isinstance(invariants, Mapping) or set(invariants) != set(REQUIRED_ARCHITECTURE_INVARIANTS):
        raise LifecycleBlockedError("architecture_invariant_missing", "required architecture invariant mapping is incomplete")
    discovered = set(behavior_proof.get("test_ids", ())) | set(lifecycle_proof.get("test_ids", ()))
    if not discovered:
        raise LifecycleBlockedError("architecture_test_mapping_invalid", "no discovered tests are available for mapping")
    for invariant in REQUIRED_ARCHITECTURE_INVARIANTS:
        test_ids = invariants[invariant].get("test_ids")
        if not isinstance(test_ids, list) or not test_ids or any(test_id not in discovered for test_id in test_ids):
            raise LifecycleBlockedError("architecture_test_mapping_invalid", f"architecture mapping is invalid: {invariant}")
    spec = mapping.get("architecture_spec")
    if not isinstance(spec, Mapping):
        raise LifecycleBlockedError("architecture_spec_binding_invalid", "architecture spec binding is missing")
    spec_path = Path(str(spec.get("path", "")))
    if not spec_path.is_absolute():
        if evidence_root is None:
            raise LifecycleBlockedError("architecture_spec_binding_invalid", "relative architecture spec lacks an evidence root")
        spec_path = Path(evidence_root).resolve() / spec_path
    if _sha256(spec_path, "architecture_spec_binding_invalid") != spec.get("sha256"):
        raise LifecycleBlockedError("architecture_spec_binding_invalid", "architecture spec digest changed")
    canonical = json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "status": "CLEAR",
        "invariant_count": len(REQUIRED_ARCHITECTURE_INVARIANTS),
        "behavior_test_count": behavior_proof["test_count"],
        "mapping_sha256": hashlib.sha256(canonical).hexdigest(),
    }
