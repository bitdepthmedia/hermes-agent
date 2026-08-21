from __future__ import annotations

import os
from pathlib import Path

import pytest

from ik_lifecycle.filesystem import (
    prepare_cell_layout,
    validate_rollback_pair,
    verify_tree_read_only,
)
from ik_lifecycle.models import LifecycleBlockedError


def test_cell_layout_creates_independent_candidate_and_release_roots(tmp_path: Path) -> None:
    ernie = prepare_cell_layout(tmp_path / "platform", "ernie")
    bert = prepare_cell_layout(tmp_path / "platform", "bert")

    assert ernie.cell_root != bert.cell_root
    assert ernie.candidates.is_dir()
    assert ernie.releases.is_dir()
    assert ernie.profiles.is_dir()
    assert ernie.backups.is_dir()
    assert ernie.receipts.is_dir()
    assert ernie.state.is_dir()


def test_candidate_root_rejects_symlink_escape(tmp_path: Path) -> None:
    platform = tmp_path / "platform"
    cell_root = platform / "cells" / "ernie"
    cell_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (cell_root / "candidates").symlink_to(outside, target_is_directory=True)

    with pytest.raises(LifecycleBlockedError) as error:
        prepare_cell_layout(platform, "ernie")

    assert error.value.code == "symlink_escape"


def test_platform_parent_symlink_is_rejected_before_creation(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    alias = tmp_path / "platform-alias"
    alias.symlink_to(outside, target_is_directory=True)

    with pytest.raises(LifecycleBlockedError) as error:
        prepare_cell_layout(alias / "platform", "ernie")

    assert error.value.code == "symlink_escape"
    assert not (outside / "platform").exists()


def test_candidate_root_rejects_running_path_overlap(tmp_path: Path) -> None:
    platform = tmp_path / "platform"
    running = platform / "cells" / "ernie" / "candidates"
    sentinel = running / "running-sentinel"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("unchanged", encoding="utf-8")

    with pytest.raises(LifecycleBlockedError) as error:
        prepare_cell_layout(platform, "ernie", protected_paths=(running,))

    assert error.value.code == "running_path_overlap"
    assert sentinel.read_text(encoding="utf-8") == "unchanged"


def test_protected_path_symlink_alias_cannot_bypass_overlap_guard(tmp_path: Path) -> None:
    platform = tmp_path / "platform"
    running = platform / "cells" / "ernie" / "candidates"
    running.mkdir(parents=True)
    alias = tmp_path / "running-alias"
    alias.symlink_to(running, target_is_directory=True)

    with pytest.raises(LifecycleBlockedError) as error:
        prepare_cell_layout(platform, "ernie", protected_paths=(alias,))

    assert error.value.code == "running_path_overlap"


def test_sealed_release_must_be_read_only(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    artifact = release / "artifact.txt"
    artifact.write_text("sealed", encoding="utf-8")
    artifact.chmod(0o644)

    with pytest.raises(LifecycleBlockedError) as error:
        verify_tree_read_only(release)

    assert error.value.code == "sealed_release_writable"
    artifact.chmod(0o444)
    release.chmod(0o555)
    verify_tree_read_only(release)


def test_rollback_pair_requires_both_valid_cell_pointers(tmp_path: Path) -> None:
    layout = prepare_cell_layout(tmp_path / "platform", "ernie")
    release = layout.releases / "old-release"
    profile = layout.profiles / "old-profile"
    release.mkdir()
    profile.mkdir()
    release_pointer = layout.cell_root / "rollback-release"
    profile_pointer = layout.cell_root / "rollback-profile"
    release_pointer.symlink_to(release, target_is_directory=True)

    with pytest.raises(LifecycleBlockedError) as error:
        validate_rollback_pair(layout, release_pointer, profile_pointer)

    assert error.value.code == "rollback_prerequisite_missing"
    profile_pointer.symlink_to(profile, target_is_directory=True)
    pair = validate_rollback_pair(layout, release_pointer, profile_pointer)
    assert pair.release == release.resolve()
    assert pair.profile == profile.resolve()


def test_cell_id_cannot_escape_platform_root(tmp_path: Path) -> None:
    with pytest.raises(LifecycleBlockedError) as error:
        prepare_cell_layout(tmp_path / "platform", "../ernie")

    assert error.value.code == "invalid_cell_id"
    assert not (tmp_path / "ernie").exists()
