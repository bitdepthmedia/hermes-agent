from __future__ import annotations

import json
import urllib.request

import pytest

from shared_core import BertModelTarget, SharedCore, create_server


def test_local_server_rejects_non_loopback_binding(tmp_path):
    core = SharedCore(tmp_path / "core.db")

    with pytest.raises(ValueError, match="loopback"):
        create_server(core, host="0.0.0.0", port=0)


def test_local_server_reports_health_and_accepts_a_task(tmp_path):
    core = SharedCore(tmp_path / "core.db")
    server = create_server(core, host="127.0.0.1", port=0)
    thread = server.start_in_thread()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base_url + "/health") as response:
            assert json.loads(response.read()) == {"status": "ok", "service": "shared-core"}

        request = urllib.request.Request(
            base_url + "/v1/tasks",
            data=json.dumps({
                "owner": "ernie",
                "session_id": "offline-1",
                "request": "classify a document",
                "action_class": "read_only",
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            assert json.loads(response.read())["owner"] == "ernie"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_local_server_sanitizes_with_an_approved_policy_rule(tmp_path):
    core = SharedCore(tmp_path / "core.db")
    rule = core.propose_policy_rule("employee-id", r"EMP-[0-9]{4}")
    core.approve_policy_rule(rule.id, reviewer="owner")
    server = create_server(core, host="127.0.0.1", port=0)
    thread = server.start_in_thread()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        request = urllib.request.Request(
            base_url + "/v1/sanitize",
            data=json.dumps({"content": "Owner EMP-1234"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            payload = json.loads(response.read())
        assert payload["content"] == "Owner [REDACTED:employee-id]"
        assert payload["finding_kinds"] == ["employee-id"]
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_bert_model_target_requires_a_provider_confirmed_id():
    target = BertModelTarget("GPT-5.6 Terra Medium", model_id="provider-confirmed-id")

    assert target.preflight(["gpt-5.4", "provider-confirmed-id"]) == "provider-confirmed-id"
    with pytest.raises(ValueError, match="not available"):
        target.preflight(["gpt-5.4"])
