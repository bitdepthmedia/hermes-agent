from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from ik_lifecycle.profile_bundle import ErnieProfileBundleInputs, build_ernie_profile_bundle, validate_ernie_profile_bundle


def _profile(root: Path, marker: str) -> None:
    root.mkdir(parents=True)
    (root / "config.yaml").write_text(yaml.safe_dump({"model": {"provider": "old", "default": "old"}}), encoding="utf-8")
    (root / "state.db").write_text(marker, encoding="utf-8")
    (root / ".env").write_text(f"PRIVATE_{marker.upper()}=secret\n", encoding="utf-8")
    for item in root.rglob("*"): os.chmod(item, 0o700 if item.is_dir() else 0o600)
    os.chmod(root, 0o700)


def test_builds_one_atomic_profile_bundle_with_two_isolated_profiles_and_opaque_credentials(tmp_path: Path) -> None:
    primary = tmp_path / "primary"; fast = tmp_path / "fast"
    _profile(primary, "primary"); _profile(fast, "fast")
    router = tmp_path / "router.env"; router.write_text("ROUTER_SECRET=red\n", encoding="utf-8"); router.chmod(0o600)
    gateway = tmp_path / "gateway.env"; gateway.write_text("GATEWAY_SECRET=red\n", encoding="utf-8"); gateway.chmod(0o600)
    shared = tmp_path / "shared.env"; shared.write_text("SHARED_SECRET=red\n", encoding="utf-8"); shared.chmod(0o600)

    result = build_ernie_profile_bundle(
        ErnieProfileBundleInputs(primary, fast, router, gateway, shared, router_port=18423),
        tmp_path / "bundles/bundle",
    )

    assert validate_ernie_profile_bundle(result.root, result.receipt).status == "CLEAR"
    for alias in ("primary", "fast"):
        config = yaml.safe_load((result.root / alias / "config.yaml").read_text(encoding="utf-8"))
        assert config["model"]["default"] == "ik-qwen38-eval:31629f53165a"
        assert config["model"]["base_url"] == "http://127.0.0.1:18423/v1"
        assert config["smart_model_routing"]["enabled"] is False
        assert "ik-persona-orchestration" in config["plugins"]["enabled"]
    assert (result.root / "router/.env").read_text() == "ROUTER_SECRET=red\n"
    assert (result.root / "compatibility-gateway/.env").read_text() == "GATEWAY_SECRET=red\n"
    assert (result.root / "compatibility-gateway/shared-core.env").read_text() == "SHARED_SECRET=red\n"
    serialized = json.dumps(result.receipt, sort_keys=True)
    assert "ROUTER_SECRET" not in serialized and "GATEWAY_SECRET" not in serialized and "PRIVATE_PRIMARY" not in serialized


def test_profile_bundle_is_idempotent_and_tamper_fails_closed(tmp_path: Path) -> None:
    primary = tmp_path / "primary"; fast = tmp_path / "fast"
    _profile(primary, "primary"); _profile(fast, "fast")
    credentials = []
    for name in ("router", "gateway", "shared"):
        path = tmp_path / f"{name}.env"; path.write_text("S=x\n", encoding="utf-8"); path.chmod(0o600); credentials.append(path)
    inputs = ErnieProfileBundleInputs(primary, fast, *credentials, router_port=18423)
    first = build_ernie_profile_bundle(inputs, tmp_path / "bundles/bundle")
    second = build_ernie_profile_bundle(inputs, tmp_path / "bundles/bundle")
    assert first.receipt == second.receipt
    target = first.root / "fast/config.yaml"; target.chmod(0o600); target.write_text("tampered", encoding="utf-8")
    try:
        validate_ernie_profile_bundle(first.root, first.receipt)
    except Exception as error:
        assert "tamper" in str(error).lower() or "changed" in str(error).lower()
    else:
        raise AssertionError("tampered bundle was accepted")
