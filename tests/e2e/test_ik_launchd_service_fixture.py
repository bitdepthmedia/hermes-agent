from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time

import pytest

from ik_lifecycle.promotion import ApprovalReceipt
from ik_lifecycle.service_control import (
    LaunchdDefinitionTransaction,
    LaunchdServiceAdapter,
    PairedSymlinks,
    promote_with_service,
    transition_with_service,
)


@pytest.mark.skipif(sys.platform != "darwin", reason="launchd fixture is macOS-only")
def test_real_launchd_fixture_preflight_restart_and_pretraffic_rollback(tmp_path: Path) -> None:
    label = f"com.ik.hermes-fixture-{os.getpid()}"
    profile = tmp_path / "profiles/old"; profile.mkdir(parents=True)
    new_profile = tmp_path / "profiles/new"; new_profile.mkdir()
    old_release = tmp_path / "releases/old"; old_release.mkdir(parents=True)
    new_release = tmp_path / "releases/new"; new_release.mkdir()
    workdir = tmp_path / "work"; workdir.mkdir()
    script = tmp_path / "fixture-service.sh"
    script.write_text("#!/bin/sh\ntrap 'exit 0' TERM INT\nwhile :; do /bin/sleep 1; done\n", encoding="utf-8")
    script.chmod(0o700)
    plist = tmp_path / f"{label}.plist"
    plist.write_bytes(plistlib.dumps({
        "Label": label,
        "ProgramArguments": [str(script)],
        "WorkingDirectory": str(workdir),
        "EnvironmentVariables": {"HERMES_HOME": str(tmp_path / "current-profile")},
        "RunAtLoad": True,
    }))
    pointers = PairedSymlinks(
        tmp_path / "current-release", tmp_path / "current-profile", tmp_path / "journal.json",
        allowed_release_root=tmp_path / "releases", allowed_profile_root=tmp_path / "profiles",
    )
    pointers.initialize(old_release, profile, 1)
    adapter = LaunchdServiceAdapter(
        label=label, plist_path=plist, expected_program=str(script), expected_workdir=str(workdir),
        expected_profile=str(tmp_path / "current-profile"), uid=os.getuid(),
    )
    domain = f"gui/{os.getuid()}"
    subprocess.run(("/bin/launchctl", "bootstrap", domain, str(plist)), check=True, timeout=15)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if adapter.preflight().running:
                    break
            except Exception:
                time.sleep(0.1)
        else:
            pytest.fail("isolated launchd fixture did not start")
        approval = ApprovalReceipt("ernie", "fixture-release", datetime.now(timezone.utc) + timedelta(minutes=2), "d" * 64)
        result = promote_with_service(
            pointers=pointers, adapter=adapter, release=str(new_release), profile=str(new_profile), generation=2,
            approval=approval, release_id="fixture-release", health=lambda: False,
        )
        assert result.status == "ROLLED_BACK_PRE_TRAFFIC"
        assert pointers.read_pair() == (str(old_release), str(profile), 1)
        assert adapter.preflight().running
    finally:
        subprocess.run(("/bin/launchctl", "bootout", domain, str(plist)), check=False, timeout=15)


@pytest.mark.skipif(sys.platform != "darwin", reason="launchd fixture is macOS-only")
def test_real_launchd_fixture_transitions_labels_and_restores_legacy_on_failure(tmp_path: Path) -> None:
    legacy_label = f"com.ik.hermes-legacy-fixture-{os.getpid()}"
    candidate_label = f"com.ik.hermes-candidate-fixture-{os.getpid()}"
    legacy_release = tmp_path / "legacy/release"; legacy_release.mkdir(parents=True)
    legacy_profile = tmp_path / "legacy/profile"; legacy_profile.mkdir(parents=True)
    new_release = tmp_path / "cell/releases/new"; new_release.mkdir(parents=True)
    new_profile = tmp_path / "cell/profiles/new"; new_profile.mkdir(parents=True)
    workdir = tmp_path / "work"; workdir.mkdir()
    script = tmp_path / "fixture-service.sh"
    script.write_text("#!/bin/sh\ntrap 'exit 0' TERM INT\nwhile :; do /bin/sleep 1; done\n", encoding="utf-8")
    script.chmod(0o700)
    legacy_plist = tmp_path / f"{legacy_label}.plist"
    candidate_source = tmp_path / f"sealed-{candidate_label}.plist"
    candidate_plist = tmp_path / "LaunchAgents" / f"{candidate_label}.plist"
    for path, label in ((legacy_plist, legacy_label), (candidate_source, candidate_label)):
        path.write_bytes(plistlib.dumps({
            "Label": label,
            "ProgramArguments": [str(script)],
            "WorkingDirectory": str(workdir),
            "EnvironmentVariables": {"HERMES_HOME": str(tmp_path / "cell/current-profile")},
            "RunAtLoad": True,
        }))
    pointers = PairedSymlinks(
        tmp_path / "cell/current", tmp_path / "cell/current-profile", tmp_path / "cell/journal.json",
        allowed_release_roots=(tmp_path / "cell/releases", legacy_release),
        allowed_profile_roots=(tmp_path / "cell/profiles", legacy_profile),
    )
    pointers.initialize(legacy_release, legacy_profile, 1)
    legacy = LaunchdServiceAdapter(
        label=legacy_label, plist_path=legacy_plist, expected_program=str(script),
        expected_workdir=str(workdir), expected_profile=str(tmp_path / "cell/current-profile"), uid=os.getuid(),
    )
    candidate = LaunchdServiceAdapter(
        label=candidate_label, plist_path=candidate_plist, expected_program=str(script),
        expected_workdir=str(workdir), expected_profile=str(tmp_path / "cell/current-profile"), uid=os.getuid(),
    )
    domain = f"gui/{os.getuid()}"
    subprocess.run(("/bin/launchctl", "bootstrap", domain, str(legacy_plist)), check=True, timeout=15)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if legacy.preflight().running:
                    break
            except Exception:
                time.sleep(0.1)
        approval = ApprovalReceipt("ernie", "candidate-id", datetime.now(timezone.utc) + timedelta(minutes=2), "e" * 64)
        result = transition_with_service(
            pointers=pointers, legacy_adapter=legacy, candidate_adapter=candidate,
            release=str(new_release), profile=str(new_profile), generation=2,
            approval=approval, release_id="candidate-id", health=lambda: False,
            definition_transaction=LaunchdDefinitionTransaction(candidate_source, candidate_plist),
            observation_timeout_seconds=0.25,
        )
        assert result.status == "ROLLED_BACK_PRE_TRAFFIC"
        assert legacy.preflight().running
        assert candidate.closed()
        assert not candidate_plist.exists()
        assert pointers.read_pair() == (str(legacy_release), str(legacy_profile), 1)
    finally:
        subprocess.run(("/bin/launchctl", "bootout", domain, str(candidate_plist)), check=False, timeout=15)
        subprocess.run(("/bin/launchctl", "bootout", domain, str(legacy_plist)), check=False, timeout=15)
