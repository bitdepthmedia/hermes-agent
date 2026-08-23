from __future__ import annotations

from pathlib import Path
import plistlib

import pytest

from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.profile_resolution import resolve_launchd_profile


def _plist(path: Path, wrapper: Path) -> None:
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": "com.ik.hermes-ernie",
                "ProgramArguments": [str(wrapper)],
                "WorkingDirectory": "/opt/ik/hermes-current",
            }
        )
    )


def test_resolves_literal_live_profile_from_authoritative_launchd_wrapper(tmp_path: Path) -> None:
    profile = tmp_path / "live-profile"
    profile.mkdir()
    wrapper = tmp_path / "start.sh"
    wrapper.write_text(f'#!/bin/sh\nexport HERMES_HOME="{profile}"\nexec hermes gateway\n', encoding="utf-8")
    plist = tmp_path / "ernie.plist"
    _plist(plist, wrapper)

    resolved = resolve_launchd_profile(plist, expected_label="com.ik.hermes-ernie")
    assert resolved.profile_root == profile.resolve()
    assert resolved.wrapper_path == wrapper.resolve()
    assert len(resolved.profile_path_sha256) == 64


def test_dynamic_expansion_symlink_or_missing_profile_fails_closed(tmp_path: Path) -> None:
    wrapper = tmp_path / "start.sh"
    plist = tmp_path / "ernie.plist"
    for declaration in (
        'export HERMES_HOME="$HOME/private"',
        'export HERMES_HOME="/missing/profile"',
    ):
        wrapper.write_text(f"#!/bin/sh\n{declaration}\n", encoding="utf-8")
        _plist(plist, wrapper)
        with pytest.raises(LifecycleBlockedError, match="profile"):
            resolve_launchd_profile(plist, expected_label="com.ik.hermes-ernie")

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    wrapper.write_text(f'export HERMES_HOME="{link}"\n', encoding="utf-8")
    _plist(plist, wrapper)
    with pytest.raises(LifecycleBlockedError, match="symlink"):
        resolve_launchd_profile(plist, expected_label="com.ik.hermes-ernie")


def test_managed_wrapper_parent_symlink_is_bound_to_resolved_target(tmp_path: Path) -> None:
    profile_parent = tmp_path / "profiles-real"
    profile_parent.mkdir()
    profile = profile_parent / "live"
    profile.mkdir()
    managed_profile_parent = tmp_path / "profiles-managed"
    managed_profile_parent.symlink_to(profile_parent, target_is_directory=True)
    real = tmp_path / "real"
    real.mkdir()
    wrapper = real / "start.sh"
    wrapper.write_text(f'export HERMES_HOME="{managed_profile_parent / "live"}"\n', encoding="utf-8")
    managed = tmp_path / "managed"
    managed.symlink_to(real, target_is_directory=True)
    plist = tmp_path / "ernie.plist"
    _plist(plist, managed / "start.sh")

    resolved = resolve_launchd_profile(plist, expected_label="com.ik.hermes-ernie")
    assert resolved.wrapper_path == wrapper.resolve()
    assert resolved.profile_root == profile.resolve()
    assert resolved.declared_wrapper_path_sha256 != resolved.wrapper_sha256
    assert resolved.declared_profile_path_sha256 != resolved.profile_path_sha256
