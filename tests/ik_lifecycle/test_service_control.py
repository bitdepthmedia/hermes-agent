from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import plistlib

import pytest

from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.promotion import ApprovalReceipt, PairedPointers
from ik_lifecycle.service_control import (
    CommandResult,
    LaunchdServiceAdapter,
    PairedSymlinks,
    ServiceGroupAdapter,
    SystemdSshServiceAdapter,
    promote_with_service,
)


class ScriptedRunner:
    def __init__(self, results: list[CommandResult]) -> None:
        self.results = list(results)
        self.argv: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...]) -> CommandResult:
        self.argv.append(argv)
        if not self.results:
            raise AssertionError(f"unexpected command: {argv}")
        return self.results.pop(0)


def _plist(path: Path, *, label: str = "com.ik.hermes-ernie") -> None:
    path.write_bytes(
        plistlib.dumps(
            {
                "Label": label,
                "ProgramArguments": ["/opt/ik/bin/start-hermes", "gateway"],
                "WorkingDirectory": "/opt/ik/hermes-current",
                "EnvironmentVariables": {"HERMES_HOME": "/opt/ik/profiles/current"},
            }
        )
    )


def test_launchd_adapter_preflight_and_control_use_exact_service_definition(tmp_path: Path) -> None:
    plist = tmp_path / "ernie.plist"
    _plist(plist)
    runner = ScriptedRunner(
        [
            CommandResult(0, "state = running\npid = 42\n", ""),
            CommandResult(0, "", ""),
            CommandResult(113, "", "not found"),
            CommandResult(0, "", ""),
            CommandResult(0, "state = running\npid = 43\n", ""),
        ]
    )
    adapter = LaunchdServiceAdapter(
        label="com.ik.hermes-ernie",
        plist_path=plist,
        expected_program="/opt/ik/bin/start-hermes",
        expected_workdir="/opt/ik/hermes-current",
        expected_profile="/opt/ik/profiles/current",
        uid=501,
        runner=runner,
    )

    assert adapter.preflight().running is True
    adapter.close()
    assert adapter.closed() is True
    adapter.open()
    assert adapter.preflight().running is True
    assert runner.argv == [
        ("/bin/launchctl", "print", "gui/501/com.ik.hermes-ernie"),
        ("/bin/launchctl", "bootout", "gui/501", str(plist)),
        ("/bin/launchctl", "print", "gui/501/com.ik.hermes-ernie"),
        ("/bin/launchctl", "bootstrap", "gui/501", str(plist)),
        ("/bin/launchctl", "print", "gui/501/com.ik.hermes-ernie"),
    ]


def test_launchd_preflight_rejects_profile_or_service_mismatch(tmp_path: Path) -> None:
    plist = tmp_path / "ernie.plist"
    _plist(plist)
    adapter = LaunchdServiceAdapter(
        label="com.ik.hermes-ernie",
        plist_path=plist,
        expected_program="/opt/ik/bin/start-hermes",
        expected_workdir="/opt/ik/hermes-current",
        expected_profile="/opt/ik/profiles/other",
        uid=501,
        runner=ScriptedRunner([CommandResult(0, "state = running", "")]),
    )
    with pytest.raises(LifecycleBlockedError, match="profile"):
        adapter.preflight()


def test_systemd_ssh_adapter_is_status_first_and_uses_no_shell_interpolation() -> None:
    runner = ScriptedRunner(
        [
            CommandResult(0, "ActiveState=active\nSubState=running\nMainPID=88\n", ""),
            CommandResult(0, "", ""),
            CommandResult(3, "ActiveState=inactive\nSubState=dead\nMainPID=0\n", ""),
            CommandResult(0, "", ""),
        ]
    )
    adapter = SystemdSshServiceAdapter(
        host="bert-live",
        unit="hermes-gateway.service",
        account="bert",
        runner=runner,
    )
    assert adapter.preflight().running is True
    adapter.close()
    assert adapter.closed() is True
    adapter.open()
    assert all(isinstance(argv, tuple) for argv in runner.argv)
    assert runner.argv[0] == (
        "/usr/bin/ssh",
        "bert-live",
        "sudo",
        "-n",
        "-u",
        "bert",
        "systemctl",
        "--user",
        "show",
        "hermes-gateway.service",
        "--property=ActiveState,SubState,MainPID",
    )


def test_failed_health_automatically_restores_pair_and_service(tmp_path: Path) -> None:
    plist = tmp_path / "ernie.plist"
    _plist(plist)
    runner = ScriptedRunner(
        [
            CommandResult(0, "state = running", ""),
            CommandResult(0, "", ""),
            CommandResult(113, "", "not found"),
            CommandResult(0, "", ""),
            CommandResult(0, "state = running", ""),
            CommandResult(0, "", ""),
            CommandResult(113, "", "not found"),
            CommandResult(0, "", ""),
            CommandResult(0, "state = running", ""),
        ]
    )
    adapter = LaunchdServiceAdapter(
        label="com.ik.hermes-ernie",
        plist_path=plist,
        expected_program="/opt/ik/bin/start-hermes",
        expected_workdir="/opt/ik/hermes-current",
        expected_profile="/opt/ik/profiles/current",
        uid=501,
        runner=runner,
    )
    pointers = PairedPointers(tmp_path / "release.json", tmp_path / "profile.json", tmp_path / "journal.json")
    pointers.initialize("old-release", "old-profile", 1)
    approval = ApprovalReceipt(
        "ernie", "new-release", datetime.now(timezone.utc) + timedelta(minutes=5), "d" * 64
    )

    receipt = promote_with_service(
        pointers=pointers,
        adapter=adapter,
        release="new-release",
        profile="new-profile",
        generation=2,
        approval=approval,
        health=lambda: False,
        observation_timeout_seconds=0.01,
    )

    assert receipt.status == "ROLLED_BACK_PRE_TRAFFIC"
    assert pointers.read_pair() == ("old-release", "old-profile", 1)


def test_paired_symlinks_switch_and_recover_only_while_service_is_closed(tmp_path: Path) -> None:
    old_release = tmp_path / "releases/old"
    new_release = tmp_path / "releases/new"
    old_profile = tmp_path / "profiles/old"
    new_profile = tmp_path / "profiles/new"
    for path in (old_release, new_release, old_profile, new_profile):
        path.mkdir(parents=True)
    pointers = PairedSymlinks(
        tmp_path / "current-release",
        tmp_path / "current-profile",
        tmp_path / "pointer-journal.json",
        allowed_release_root=tmp_path / "releases",
        allowed_profile_root=tmp_path / "profiles",
    )
    pointers.initialize(old_release, old_profile, 1)
    pointers.switch(new_release, new_profile, 2, service_closed=True)
    assert pointers.read_pair() == (str(new_release), str(new_profile), 2)

    with pytest.raises(RuntimeError, match="injected"):
        pointers.switch(old_release, old_profile, 3, service_closed=True, crash_after_release=True)
    pointers.recover(service_closed=True)
    assert pointers.read_pair() == (str(new_release), str(new_profile), 2)

    with pytest.raises(LifecycleBlockedError, match="closed"):
        pointers.switch(old_release, old_profile, 4, service_closed=False)


def test_paired_symlinks_reject_targets_outside_cell_roots(tmp_path: Path) -> None:
    release_root = tmp_path / "releases"
    profile_root = tmp_path / "profiles"
    release_root.mkdir(); profile_root.mkdir()
    valid_profile = profile_root / "ok"; valid_profile.mkdir()
    pointers = PairedSymlinks(
        tmp_path / "current-release",
        tmp_path / "current-profile",
        tmp_path / "journal.json",
        allowed_release_root=release_root,
        allowed_profile_root=profile_root,
    )
    with pytest.raises(LifecycleBlockedError, match="target"):
        pointers.initialize(tmp_path / "outside", valid_profile, 1)


def test_failed_health_restores_real_symlink_pair(tmp_path: Path) -> None:
    old_release = tmp_path / "releases/old"; old_release.mkdir(parents=True)
    new_release = tmp_path / "releases/new"; new_release.mkdir()
    old_profile = tmp_path / "profiles/old"; old_profile.mkdir(parents=True)
    new_profile = tmp_path / "profiles/new"; new_profile.mkdir()
    pointers = PairedSymlinks(
        tmp_path / "current-release", tmp_path / "current-profile", tmp_path / "journal.json",
        allowed_release_root=tmp_path / "releases", allowed_profile_root=tmp_path / "profiles",
    )
    pointers.initialize(old_release, old_profile, 1)
    plist = tmp_path / "ernie.plist"; _plist(plist)
    runner = ScriptedRunner([
        CommandResult(0, "state = running", ""), CommandResult(0, "", ""), CommandResult(113, "", ""),
        CommandResult(0, "", ""), CommandResult(0, "state = running", ""),
        CommandResult(0, "", ""), CommandResult(113, "", ""), CommandResult(0, "", ""),
        CommandResult(0, "state = running", ""),
    ])
    adapter = LaunchdServiceAdapter(
        label="com.ik.hermes-ernie", plist_path=plist,
        expected_program="/opt/ik/bin/start-hermes", expected_workdir="/opt/ik/hermes-current",
        expected_profile="/opt/ik/profiles/current", uid=501, runner=runner,
    )
    approval = ApprovalReceipt("ernie", "release-id", datetime.now(timezone.utc) + timedelta(minutes=5), "d" * 64)
    receipt = promote_with_service(
        pointers=pointers, adapter=adapter, release=str(new_release), profile=str(new_profile), generation=2,
        approval=approval, release_id="release-id", health=lambda: False, observation_timeout_seconds=0.01,
    )
    assert receipt.status == "ROLLED_BACK_PRE_TRAFFIC"
    assert pointers.read_pair() == (str(old_release), str(old_profile), 1)


def test_service_group_starts_model_before_gateway_and_stops_in_reverse() -> None:
    events: list[str] = []

    class Fake:
        def __init__(self, name: str) -> None: self.name = name; self.running = True
        def preflight(self):
            from ik_lifecycle.service_control import ServicePreflight
            return ServicePreflight(self.running, "running" if self.running else "unloaded")
        def close(self): events.append(f"close:{self.name}"); self.running = False
        def closed(self): return not self.running
        def open(self): events.append(f"open:{self.name}"); self.running = True

    model, gateway = Fake("model"), Fake("gateway")
    group = ServiceGroupAdapter((model, gateway))
    assert group.preflight().running
    group.close(); assert group.closed()
    group.open(); assert group.preflight().running
    assert events == ["close:gateway", "close:model", "open:model", "open:gateway"]
