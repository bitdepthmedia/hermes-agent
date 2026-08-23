from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.profile_candidate import build_ernie_profile_candidate, validate_ernie_profile_candidate


def _source(root: Path) -> Path:
    source = root / "migrated"
    source.mkdir(parents=True)
    (source / "state.db").write_bytes(b"opaque-state")
    (source / ".env").write_text("SYNTHETIC_API_KEY=synthetic-secret-value\n", encoding="utf-8")
    (source / "config.yaml").write_text(
        yaml.safe_dump({"model": {"provider": "legacy", "default": "legacy-model"}, "plugins": {"enabled": ["existing"]}}),
        encoding="utf-8",
    )
    os.chmod(source, 0o700)
    for path in source.iterdir():
        os.chmod(path, 0o600)
    return source


def test_builds_private_profile_with_exact_primary_and_supported_plugin(tmp_path: Path) -> None:
    destination = tmp_path / "cell/profiles/candidate"
    receipt = build_ernie_profile_candidate(_source(tmp_path), destination, model_port=18422)
    config = yaml.safe_load((destination / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"] == {
        "api_mode": "chat_completions",
        "base_url": "http://127.0.0.1:18422/v1",
        "default": "ik-qwen38-eval:31629f53165a",
        "model": "ik-qwen38-eval:31629f53165a",
        "provider": "ik-ernie-local",
    }
    assert config["smart_model_routing"]["enabled"] is False
    assert config["plugins"]["enabled"] == ["existing", "ik-persona-orchestration"]
    assert receipt["status"] == "CLEAR_PROFILE_CANDIDATE"
    assert "synthetic-secret" not in json.dumps(receipt)
    assert validate_ernie_profile_candidate(destination, receipt) == "CLEAR"
    assert oct(os.stat(destination / ".env").st_mode & 0o777) == "0o600"


def test_profile_candidate_is_idempotent_and_tamper_fails_closed(tmp_path: Path) -> None:
    source = _source(tmp_path)
    destination = tmp_path / "cell/profiles/candidate"
    receipt = build_ernie_profile_candidate(source, destination, model_port=18422)
    assert build_ernie_profile_candidate(source, destination, model_port=18422) == receipt
    (destination / "state.db").write_bytes(b"tampered")
    with pytest.raises(LifecycleBlockedError, match="profile candidate"):
        validate_ernie_profile_candidate(destination, receipt)


def test_profile_candidate_rejects_symlinks_and_non_loopback_port(tmp_path: Path) -> None:
    source = _source(tmp_path)
    (source / "linked").symlink_to(source / "state.db")
    with pytest.raises(LifecycleBlockedError):
        build_ernie_profile_candidate(source, tmp_path / "candidate", model_port=18422)
    with pytest.raises(LifecycleBlockedError):
        build_ernie_profile_candidate(_source(tmp_path / "other"), tmp_path / "candidate-2", model_port=80)
