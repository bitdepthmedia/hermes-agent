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
    LaunchdDefinitionTransaction,
    ServiceGroupAdapter,
    SystemdSshServiceAdapter,
    promote_with_service,
    transition_with_service,
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


def test_launchd_adapter_supports_separately_verified_wrapper_profile(tmp_path: Path) -> None:
    plist = tmp_path / "legacy.plist"
    plist.write_bytes(plistlib.dumps({
        "Label": "com.ik.hermes-legacy",
        "ProgramArguments": ["/opt/ik/bin/legacy-wrapper"],
        "WorkingDirectory": "/opt/ik/legacy",
    }))
    adapter = LaunchdServiceAdapter(
        label="com.ik.hermes-legacy", plist_path=plist,
        expected_program="/opt/ik/bin/legacy-wrapper", expected_workdir="/opt/ik/legacy",
        expected_profile=None, uid=501,
        runner=ScriptedRunner([CommandResult(0, "state = running", "")]),
    )
    assert adapter.preflight().running


def test_systemd_ssh_adapter_is_status_first_and_uses_no_shell_interpolation() -> None:
    runner = ScriptedRunner(
        [
            CommandResult(0, "ActiveState=active\nSubState=running\nMainPID=88\n", ""),
            CommandResult(0, "", ""),
            CommandResult(0, "ActiveState=inactive\nSubState=dead\nMainPID=0\n", ""),
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


def test_systemd_ssh_adapter_supports_documented_system_service_scope() -> None:
    unit_sha = "a" * 64
    runner = ScriptedRunner([
        CommandResult(
            0,
            "ActiveState=active\nSubState=running\nMainPID=88\nUser=bert\n"
            "ExecStart={ path=/opt/ik/bert/bin/ik-bert-cell-service ; argv[]=/opt/ik/bert/bin/ik-bert-cell-service ; }\n"
            "FragmentPath=/etc/systemd/system/hermes-gateway.service\n"
            "DropInPaths=\n"
            "Environment=HERMES_HOME=/opt/ik/bert/current-profile\n",
            "",
        ),
        CommandResult(0, f"{unit_sha}  /etc/systemd/system/hermes-gateway.service\n", ""),
    ])
    adapter = SystemdSshServiceAdapter(
        host="bert-live",
        unit="hermes-gateway.service",
        account="bert",
        scope="system",
        expected_program="/opt/ik/bert/bin/ik-bert-cell-service",
        expected_profile="/opt/ik/bert/current-profile",
        expected_unit_sha256=unit_sha,
        runner=runner,
    )

    assert adapter.preflight().running
    assert runner.argv == [
        (
            "/usr/bin/ssh",
            "bert-live",
            "sudo",
            "-n",
            "systemctl",
            "show",
            "hermes-gateway.service",
            "--property=ActiveState,SubState,MainPID,User,ExecStart,FragmentPath,DropInPaths,Environment",
        ),
        (
            "/usr/bin/ssh",
            "bert-live",
            "sudo",
            "-n",
            "sha256sum",
            "/etc/systemd/system/hermes-gateway.service",
        ),
    ]


def test_systemd_closed_requires_positive_inactive_state_not_transport_failure() -> None:
    unit_sha = "a" * 64
    adapter = SystemdSshServiceAdapter(
        host="bert-live", unit="hermes-gateway.service", account="bert", scope="system",
        expected_program="/opt/ik/bert/bin/ik-bert-cell-service",
        expected_profile="/opt/ik/bert/current-profile", expected_unit_sha256=unit_sha,
        runner=ScriptedRunner([CommandResult(255, "", "transport failed")]),
    )

    with pytest.raises(LifecycleBlockedError, match="state"):
        adapter.closed()


def test_systemd_system_scope_rejects_dropins_even_when_fragment_digest_matches() -> None:
    unit_sha = "a" * 64
    runner = ScriptedRunner([CommandResult(
        0,
        "ActiveState=active\nSubState=running\nMainPID=88\nUser=bert\n"
        "ExecStart={ path=/opt/ik/bert/bin/ik-bert-cell-service ; argv[]=/opt/ik/bert/bin/ik-bert-cell-service ; }\n"
        "FragmentPath=/etc/systemd/system/hermes-gateway.service\n"
        "DropInPaths=/etc/systemd/system/hermes-gateway.service.d/override.conf\n"
        "Environment=HERMES_HOME=/opt/ik/bert/current-profile\n",
        "",
    )])
    adapter = SystemdSshServiceAdapter(
        host="bert-live", unit="hermes-gateway.service", account="bert", scope="system",
        expected_program="/opt/ik/bert/bin/ik-bert-cell-service",
        expected_profile="/opt/ik/bert/current-profile", expected_unit_sha256=unit_sha,
        runner=runner,
    )

    with pytest.raises(LifecycleBlockedError, match="definition"):
        adapter.preflight()


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


def test_paired_symlinks_allow_declared_legacy_rollback_roots(tmp_path: Path) -> None:
    legacy_release = tmp_path / "legacy-release"; legacy_release.mkdir()
    legacy_profile = tmp_path / "legacy-profile"; legacy_profile.mkdir()
    release_root = tmp_path / "cell/releases"; release_root.mkdir(parents=True)
    profile_root = tmp_path / "cell/profiles"; profile_root.mkdir(parents=True)
    pointers = PairedSymlinks(
        tmp_path / "cell/current", tmp_path / "cell/current-profile", tmp_path / "cell/journal.json",
        allowed_release_roots=(release_root, legacy_release),
        allowed_profile_roots=(profile_root, legacy_profile),
    )
    pointers.initialize(legacy_release, legacy_profile, 1)
    assert pointers.read_pair() == (str(legacy_release), str(legacy_profile), 1)


def test_launchd_definition_transaction_installs_exact_file_and_can_restore(tmp_path: Path) -> None:
    source = tmp_path / "sealed.plist"
    destination = tmp_path / "LaunchAgents/candidate.plist"
    source.write_bytes(b"sealed-definition")
    transaction = LaunchdDefinitionTransaction(source, destination)

    transaction.prepare()
    assert destination.read_bytes() == b"sealed-definition"
    transaction.rollback()
    assert not destination.exists()


def test_launchd_definition_transaction_rejects_destination_drift(tmp_path: Path) -> None:
    source = tmp_path / "sealed.plist"; source.write_bytes(b"sealed-definition")
    destination = tmp_path / "candidate.plist"; destination.write_bytes(b"other")
    transaction = LaunchdDefinitionTransaction(source, destination)
    with pytest.raises(LifecycleBlockedError, match="definition"):
        transaction.prepare()


def test_launchd_definition_transaction_rejects_symlink_source(tmp_path: Path) -> None:
    real = tmp_path / "real.plist"; real.write_bytes(b"sealed-definition")
    source = tmp_path / "sealed.plist"; source.symlink_to(real)
    transaction = LaunchdDefinitionTransaction(source, tmp_path / "candidate.plist")
    with pytest.raises(LifecycleBlockedError, match="definition"):
        transaction.prepare()


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


def test_service_group_enforces_bounded_port_quiescence_before_reopen() -> None:
    events: list[str] = []
    clock = iter((100.0, 115.0))

    class Fake:
        running = True
        def preflight(self):
            from ik_lifecycle.service_control import ServicePreflight
            return ServicePreflight(self.running, "running" if self.running else "unloaded")
        def close(self): self.running = False
        def closed(self): return not self.running
        def open(self): events.append("open"); self.running = True

    group = ServiceGroupAdapter(
        (Fake(),),
        quiescence_seconds=70.0,
        monotonic=lambda: next(clock),
        sleeper=lambda seconds: events.append(f"sleep:{seconds}"),
    )
    group.close()
    group.open()

    assert events == ["sleep:55.0", "open"]


def test_service_transition_stops_legacy_switches_pair_and_starts_candidate(tmp_path: Path) -> None:
    old_release = tmp_path / "legacy/release"; old_release.mkdir(parents=True)
    old_profile = tmp_path / "legacy/profile"; old_profile.mkdir(parents=True)
    new_release = tmp_path / "cell/releases/new"; new_release.mkdir(parents=True)
    new_profile = tmp_path / "cell/profiles/new"; new_profile.mkdir(parents=True)
    pointers = PairedSymlinks(
        tmp_path / "cell/current", tmp_path / "cell/current-profile", tmp_path / "cell/journal.json",
        allowed_release_roots=(tmp_path / "cell/releases", old_release),
        allowed_profile_roots=(tmp_path / "cell/profiles", old_profile),
    )
    pointers.initialize(old_release, old_profile, 1)
    events: list[str] = []

    class Fake:
        def __init__(self, name: str, running: bool) -> None: self.name=name; self.running=running
        def preflight(self):
            from ik_lifecycle.service_control import ServicePreflight
            return ServicePreflight(self.running, "running" if self.running else "unloaded")
        def close(self): events.append(f"close:{self.name}"); self.running=False
        def closed(self): return not self.running
        def open(self): events.append(f"open:{self.name}"); self.running=True

    legacy, candidate = Fake("legacy", True), Fake("candidate", False)
    approval = ApprovalReceipt("ernie", "new-id", datetime.now(timezone.utc) + timedelta(minutes=5), "d" * 64)
    result = transition_with_service(
        pointers=pointers, legacy_adapter=legacy, candidate_adapter=candidate,
        release=str(new_release), profile=str(new_profile), generation=2,
        approval=approval, release_id="new-id", health=lambda: True,
        observation_timeout_seconds=0.01,
    )
    assert result.status == "PROMOTED_CLEAR"
    assert events == ["close:legacy", "open:candidate"]
    assert pointers.read_pair() == (str(new_release), str(new_profile), 2)


def test_failed_service_transition_restores_legacy_pair_and_service(tmp_path: Path) -> None:
    old_release = tmp_path / "legacy/release"; old_release.mkdir(parents=True)
    old_profile = tmp_path / "legacy/profile"; old_profile.mkdir(parents=True)
    new_release = tmp_path / "cell/releases/new"; new_release.mkdir(parents=True)
    new_profile = tmp_path / "cell/profiles/new"; new_profile.mkdir(parents=True)
    pointers = PairedSymlinks(
        tmp_path / "cell/current", tmp_path / "cell/current-profile", tmp_path / "cell/journal.json",
        allowed_release_roots=(tmp_path / "cell/releases", old_release),
        allowed_profile_roots=(tmp_path / "cell/profiles", old_profile),
    )
    pointers.initialize(old_release, old_profile, 1)
    events: list[str] = []
    class Fake:
        def __init__(self, name: str, running: bool) -> None: self.name=name; self.running=running
        def preflight(self):
            from ik_lifecycle.service_control import ServicePreflight
            return ServicePreflight(self.running, "running" if self.running else "unloaded")
        def close(self): events.append(f"close:{self.name}"); self.running=False
        def closed(self): return not self.running
        def open(self): events.append(f"open:{self.name}"); self.running=True
    legacy, candidate = Fake("legacy", True), Fake("candidate", False)
    approval = ApprovalReceipt("ernie", "new-id", datetime.now(timezone.utc) + timedelta(minutes=5), "d" * 64)
    result = transition_with_service(
        pointers=pointers, legacy_adapter=legacy, candidate_adapter=candidate,
        release=str(new_release), profile=str(new_profile), generation=2,
        approval=approval, release_id="new-id", health=lambda: False,
        observation_timeout_seconds=0.01,
    )
    assert result.status == "ROLLED_BACK_PRE_TRAFFIC"
    assert events == ["close:legacy", "open:candidate", "close:candidate", "open:legacy"]
    assert pointers.read_pair() == (str(old_release), str(old_profile), 1)
