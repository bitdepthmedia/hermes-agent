from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

import pytest

from ik_lifecycle import cli


def test_malformed_plan_fails_closed_without_traceback(tmp_path, capsys):
    plan = tmp_path / "invalid.json"
    plan.write_text("[]", encoding="utf-8")
    assert (
        cli.main([
            "seal",
            "--plan",
            str(plan),
            "--approve-sha256",
            hashlib.sha256(plan.read_bytes()).hexdigest(),
        ])
        == 2
    )
    assert json.loads(capsys.readouterr().err)["status"] == "BLOCKED"


def test_execution_plan_requires_separate_bound_reviews(tmp_path):
    from ik_lifecycle.guarded_release import read_approved_plan, review_subject
    from ik_lifecycle.models import LifecycleBlockedError

    plan = tmp_path / "plan.json"
    data = {
        "schema_id": "ik.hermes.guarded-release.v1",
        "operation": "seal",
        "authority": "approved task",
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }

    def read():
        plan.write_text(json.dumps(data))
        return read_approved_plan(
            plan, hashlib.sha256(plan.read_bytes()).hexdigest(), "seal"
        )

    with pytest.raises(LifecycleBlockedError, match="review"):
        read()
    review = tmp_path / "review.json"
    review.write_text(
        json.dumps({
            "status": "CLEAR",
            "authority": "separate review approval",
            "subject_sha256": review_subject(data),
        })
    )
    binding = {
        "path": str(review),
        "sha256": hashlib.sha256(review.read_bytes()).hexdigest(),
    }
    data["reviews"] = {"supply_chain": binding, "privacy": binding}
    assert read()["operation"] == "seal"
    data["release_root"] = "/changed"
    with pytest.raises(LifecycleBlockedError, match="review"):
        read()
    del data["release_root"]
    review.write_text("{}")
    with pytest.raises(LifecycleBlockedError, match="review"):
        read()


def test_release_commands_require_exact_plan_approval_before_execution(
    tmp_path, capsys
):
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({
            "schema_id": "ik.hermes.guarded-release.v1",
            "operation": "seal",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat(),
        })
    )
    try:
        result = cli.main(["seal", "--plan", str(plan), "--approve-sha256", "0" * 64])
    except SystemExit as exc:
        pytest.fail(
            f"guarded seal must fail with a structured gate result, not missing command: {exc}"
        )
    assert result == 2
    assert json.loads(capsys.readouterr().err)["code"] == "release_plan_approval"


def test_drift_audit_separates_currency_from_integrity(tmp_path):
    from ik_lifecycle import health

    classify = getattr(health, "classify_release_drift", None)
    assert classify is not None, (
        "audit must distinguish release lag from source divergence"
    )
    assert (
        classify(
            integrity="CLEAR",
            deployed_target="old",
            required_target="new",
            provenance=True,
        )
        == "UPGRADE_PENDING_APPROVAL"
    )
    assert (
        classify(
            integrity="BLOCKED",
            deployed_target="new",
            required_target="new",
            provenance=True,
        )
        == "SOURCE_DIVERGENCE"
    )
    assert (
        classify(
            integrity="CLEAR",
            deployed_target="new",
            required_target="new",
            provenance=False,
        )
        == "UNVERIFIED"
    )
    assert (
        classify(
            integrity="CLEAR",
            deployed_target="new",
            required_target="new",
            provenance=True,
        )
        == "CLEAR"
    )
