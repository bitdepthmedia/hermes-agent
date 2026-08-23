from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from ik_lifecycle.profile_bundle import (
    ErnieProfileBundleInputs,
    build_ernie_profile_bundle,
    validate_ernie_profile_bundle,
)


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
        ErnieProfileBundleInputs(
            primary,
            fast,
            router,
            gateway,
            shared,
            router_port=18423,
            fast_port=18424,
            primary_port=18425,
        ),
        tmp_path / "bundles/bundle",
    )

    assert validate_ernie_profile_bundle(result.root, result.receipt).status == "CLEAR"
    for alias in ("primary", "fast"):
        config = yaml.safe_load((result.root / alias / "config.yaml").read_text(encoding="utf-8"))
        assert config["model"]["default"] == "ik-qwen38-eval:31629f53165a"
        assert config["model"]["base_url"] == "http://127.0.0.1:18423/v1"
        assert config["smart_model_routing"]["enabled"] is False
        assert "ik-persona-orchestration" in config["plugins"]["enabled"]
        expected_port = 18425 if alias == "primary" else 18424
        assert config["platforms"]["api_server"]["extra"] == {
            "host": "127.0.0.1",
            "port": expected_port,
        }
    assert (result.root / "router/.env").read_text() == "ROUTER_SECRET=red\n"
    assert (result.root / "compatibility-gateway/.env").read_text() == "GATEWAY_SECRET=red\n"
    assert (result.root / "compatibility-gateway/shared-core.env").read_text() == "SHARED_SECRET=red\n"
    serialized = json.dumps(result.receipt, sort_keys=True)
    assert "ROUTER_SECRET" not in serialized and "GATEWAY_SECRET" not in serialized and "PRIVATE_PRIMARY" not in serialized


def test_profile_bundle_removes_only_runtime_control_env_keys(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    fast = tmp_path / "fast"
    _profile(primary, "primary")
    _profile(fast, "fast")
    for profile in (primary, fast):
        (profile / ".env").write_text(
            "PRIVATE_TOKEN=keep-secret\nAPI_SERVER_PORT=8644\nIK_MODEL_BASE_URL=http://stale.invalid/v1\n"
            "TELEGRAM_BOT_TOKEN=must-remain-outside-api-worker\n",
            encoding="utf-8",
        )
        (profile / ".env").chmod(0o600)
    credentials = []
    for name in ("router", "gateway", "shared"):
        path = tmp_path / f"{name}.env"
        path.write_text("S=x\n", encoding="utf-8")
        path.chmod(0o600)
        credentials.append(path)

    result = build_ernie_profile_bundle(
        ErnieProfileBundleInputs(
            primary,
            fast,
            *credentials,
            router_port=18423,
            fast_port=18424,
            primary_port=18425,
        ),
        tmp_path / "bundles/bundle",
    )

    for alias in ("primary", "fast"):
        environment = (result.root / alias / ".env").read_text(encoding="utf-8")
        assert "PRIVATE_TOKEN=keep-secret" in environment
        assert "API_SERVER_PORT" not in environment
        assert "IK_MODEL_BASE_URL" not in environment
        assert "TELEGRAM_BOT_TOKEN" not in environment
        config = yaml.safe_load((result.root / alias / "config.yaml").read_text(encoding="utf-8"))
        assert set(config["platforms"]) == {"api_server"}


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


def test_fast_profile_may_materialize_only_links_into_primary_profile(tmp_path: Path) -> None:
    primary = tmp_path / "primary"; fast = tmp_path / "fast"
    _profile(primary, "primary"); _profile(fast, "fast")
    (fast / "shared-persona").symlink_to(primary / "config.yaml")
    credentials = []
    for name in ("router", "gateway", "shared"):
        path = tmp_path / f"{name}.env"; path.write_text("S=x\n", encoding="utf-8"); path.chmod(0o600); credentials.append(path)

    result = build_ernie_profile_bundle(
        ErnieProfileBundleInputs(primary, fast, *credentials, router_port=18423),
        tmp_path / "bundles/bundle",
    )

    assert (result.root / "fast/shared-persona").is_file()
    assert not (result.root / "fast/shared-persona").is_symlink()


def test_fast_profile_link_outside_primary_fails_closed(tmp_path: Path) -> None:
    primary = tmp_path / "primary"; fast = tmp_path / "fast"
    _profile(primary, "primary"); _profile(fast, "fast")
    outside = tmp_path / "outside"; outside.write_text("private", encoding="utf-8")
    (fast / "escape").symlink_to(outside)
    credentials = []
    for name in ("router", "gateway", "shared"):
        path = tmp_path / f"{name}.env"; path.write_text("S=x\n", encoding="utf-8"); path.chmod(0o600); credentials.append(path)

    with pytest.raises(Exception, match="symlink"):
        build_ernie_profile_bundle(
            ErnieProfileBundleInputs(primary, fast, *credentials, router_port=18423),
            tmp_path / "bundles/bundle",
        )


def test_legacy_read_only_credential_handle_is_restricted_in_bundle(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    fast = tmp_path / "fast"
    _profile(primary, "primary")
    _profile(fast, "fast")
    credentials = []
    for name in ("router", "gateway", "shared"):
        path = tmp_path / f"{name}.env"
        path.write_text("S=x\n", encoding="utf-8")
        path.chmod(0o644)
        credentials.append(path)

    result = build_ernie_profile_bundle(
        ErnieProfileBundleInputs(primary, fast, *credentials, router_port=18423),
        tmp_path / "bundles/bundle",
    )

    assert (result.root / "router/.env").stat().st_mode & 0o777 == 0o600
    assert (result.root / "compatibility-gateway/.env").stat().st_mode & 0o777 == 0o600
    assert (result.root / "compatibility-gateway/shared-core.env").stat().st_mode & 0o777 == 0o600


def test_group_writable_credential_handle_fails_closed(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    fast = tmp_path / "fast"
    _profile(primary, "primary")
    _profile(fast, "fast")
    credentials = []
    for name, mode in (("router", 0o600), ("gateway", 0o620), ("shared", 0o600)):
        path = tmp_path / f"{name}.env"
        path.write_text("S=x\n", encoding="utf-8")
        path.chmod(mode)
        credentials.append(path)

    with pytest.raises(Exception, match="credential"):
        build_ernie_profile_bundle(
            ErnieProfileBundleInputs(primary, fast, *credentials, router_port=18423),
            tmp_path / "bundles/bundle",
        )


def test_legacy_read_only_profile_modes_are_restricted_in_bundle(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    fast = tmp_path / "fast"
    _profile(primary, "primary")
    _profile(fast, "fast")
    (fast / "legacy-dir").mkdir(mode=0o755)
    (fast / "legacy-dir/value").write_text("opaque", encoding="utf-8")
    (fast / "legacy-dir/value").chmod(0o644)
    credentials = []
    for name in ("router", "gateway", "shared"):
        path = tmp_path / f"{name}.env"
        path.write_text("S=x\n", encoding="utf-8")
        path.chmod(0o600)
        credentials.append(path)

    result = build_ernie_profile_bundle(
        ErnieProfileBundleInputs(primary, fast, *credentials, router_port=18423),
        tmp_path / "bundles/bundle",
    )

    assert (result.root / "fast/legacy-dir").stat().st_mode & 0o777 == 0o700
    assert (result.root / "fast/legacy-dir/value").stat().st_mode & 0o777 == 0o600


def test_group_writable_profile_source_fails_closed(tmp_path: Path) -> None:
    primary = tmp_path / "primary"
    fast = tmp_path / "fast"
    _profile(primary, "primary")
    _profile(fast, "fast")
    (fast / "config.yaml").chmod(0o620)
    credentials = []
    for name in ("router", "gateway", "shared"):
        path = tmp_path / f"{name}.env"
        path.write_text("S=x\n", encoding="utf-8")
        path.chmod(0o600)
        credentials.append(path)

    with pytest.raises(Exception, match="permissions"):
        build_ernie_profile_bundle(
            ErnieProfileBundleInputs(primary, fast, *credentials, router_port=18423),
            tmp_path / "bundles/bundle",
        )


def test_fast_links_may_bind_to_a_distinct_authoritative_primary_root(tmp_path: Path) -> None:
    migrated_primary = tmp_path / "migrated-primary"
    live_primary = tmp_path / "live-primary"
    fast = tmp_path / "fast"
    _profile(migrated_primary, "migrated")
    _profile(live_primary, "live")
    _profile(fast, "fast")
    (fast / "shared-persona").symlink_to(live_primary / "config.yaml")
    credentials = []
    for name in ("router", "gateway", "shared"):
        path = tmp_path / f"{name}.env"
        path.write_text("S=x\n", encoding="utf-8")
        path.chmod(0o600)
        credentials.append(path)

    result = build_ernie_profile_bundle(
        ErnieProfileBundleInputs(
            migrated_primary,
            fast,
            *credentials,
            router_port=18423,
            fast_link_root=live_primary,
        ),
        tmp_path / "bundles/bundle",
    )

    assert (result.root / "fast/shared-persona").is_file()
