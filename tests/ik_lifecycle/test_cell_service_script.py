from __future__ import annotations

from pathlib import Path
import subprocess


SCRIPT = Path(__file__).parents[2] / "scripts/ik-cell-service"


def test_cell_service_uses_only_paired_cell_pointers_and_frozen_runtime() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert subprocess.run(["/bin/sh", "-n", str(SCRIPT)], check=False).returncode == 0
    assert '"$cell_root/current-release"' in source
    assert '"$cell_root/current-profile/primary"' in source
    assert 'surfaces/python-runtime/bin/python' in source
    assert '/usr/bin/hdiutil attach -nobrowse -mountpoint' in source
    assert '"$IK_RELEASE_IMAGE"' in source
    assert "hermes_cli.main gateway run --replace" in source
    assert "~/.hermes" not in source
    assert "pip " not in source and "npm " not in source and "uv " not in source


def test_cell_service_dispatches_the_declared_full_ernie_topology() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "shared-release.path" in source
    assert 'IK_CELL_SERVICE_ROLE' in source
    assert 'router_service:app' in source
    assert 'compatibility-gateway' in source
    assert 'exec "$model_runtime" serve' in source
    assert 'IK_CELL_CREDENTIAL_FILE' in source
    assert 'IK_CELL_SHARED_CREDENTIAL_FILE' in source
    assert 'eval' not in source


def test_cell_service_does_not_remount_an_existing_runtime_volume() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    directory_guard = source.index('if [ ! -d "$IK_RELEASE_MOUNT" ]')
    attach = source.index('/usr/bin/hdiutil attach -nobrowse -mountpoint')
    assert directory_guard < attach
