from __future__ import annotations

from pathlib import Path

import pytest

from ik_lifecycle.case_sensitive_store import CaseSensitiveReleaseStore, StoreCommandResult
from ik_lifecycle.models import LifecycleBlockedError


class Runner:
    def __init__(self, results: list[StoreCommandResult]) -> None:
        self.results = results
        self.argv: list[tuple[str, ...]] = []
    def __call__(self, argv: tuple[str, ...]) -> StoreCommandResult:
        self.argv.append(argv)
        return self.results.pop(0)


def test_store_uses_exact_hdiutil_create_and_mount_commands(tmp_path: Path) -> None:
    cell = tmp_path / "cell"; cell.mkdir()
    image = cell / "release-store.sparsebundle"; mount = cell / "runtime-volume"
    runner = Runner([StoreCommandResult(0, "", ""), StoreCommandResult(0, "", "")])
    store = CaseSensitiveReleaseStore(cell, image, mount, runner=runner, case_sensitive_probe=lambda _: True, materialized_probe=lambda *_: True)
    receipt = store.create_and_mount(size_gib=12, volume_name="HermesPlatformRuntimeV1")
    assert receipt.status == "CLEAR_CASE_SENSITIVE_RELEASE_STORE"
    assert runner.argv == [
        ("/usr/bin/hdiutil", "create", "-type", "SPARSEBUNDLE", "-fs", "APFSX", "-size", "12g", "-volname", "HermesPlatformRuntimeV1", str(image)),
        ("/usr/bin/hdiutil", "attach", "-nobrowse", "-mountpoint", str(mount), str(image)),
    ]


def test_store_fails_closed_on_command_case_or_scope_mismatch(tmp_path: Path) -> None:
    cell = tmp_path / "cell"; cell.mkdir()
    with pytest.raises(LifecycleBlockedError, match="inside"):
        CaseSensitiveReleaseStore(cell, tmp_path / "outside.sparsebundle", cell / "mount")
    store = CaseSensitiveReleaseStore(
        cell, cell / "store.sparsebundle", cell / "mount",
        runner=Runner([StoreCommandResult(0, "", ""), StoreCommandResult(0, "", "")]),
        case_sensitive_probe=lambda _: False,
        materialized_probe=lambda *_: True,
    )
    with pytest.raises(LifecycleBlockedError, match="case-sensitive"):
        store.create_and_mount(size_gib=12, volume_name="HermesPlatformRuntimeV1")
