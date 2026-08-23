from __future__ import annotations

from pathlib import Path
import os
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
    assert 'run_loopback_only "$model_runtime" serve' in source
    assert 'IK_CELL_CREDENTIAL_FILE' in source
    assert 'IK_CELL_SHARED_CREDENTIAL_FILE' in source
    assert 'eval' not in source
    assert '. "$credential_file"' not in source
    assert 'ik_lifecycle.credential_exec' in source
    assert '[ "$IK_CELL_CREDENTIAL_FILE" = "$cell_root/current-profile/router/.env" ] || exit 87' in source
    assert '[ "$IK_CELL_CREDENTIAL_FILE" = "$cell_root/current-profile/compatibility-gateway/.env" ] || exit 87' in source
    assert '[ "$IK_CELL_SHARED_CREDENTIAL_FILE" = "$cell_root/current-profile/compatibility-gateway/shared-core.env" ] || exit 87' in source
    assert '--policy router' in source
    assert '--policy compatibility' in source


def test_cell_service_does_not_remount_an_existing_runtime_volume() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    directory_guard = source.index('if [ ! -d "$IK_RELEASE_MOUNT" ]')
    attach = source.index('/usr/bin/hdiutil attach -nobrowse -mountpoint')
    assert directory_guard < attach


def test_model_and_router_are_always_loopback_sandboxed_in_live_service() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert 'run_loopback_only "$model_runtime" serve' in source
    assert 'run_loopback_only "$python" -m ik_lifecycle.credential_exec' in source
    assert '"$python" -m uvicorn ik_extensions.model_workers.router_service:app' in source
    assert '[ -x /usr/bin/sandbox-exec ] || exit 85' in source
    assert '(deny network*)' in source
    assert '(allow network-outbound (remote ip "localhost:*"))' in source
    assert 'exec /usr/bin/sandbox-exec -p "$policy" "$@"' in source
    assert '[ "$IK_SERVICE_HOST" = "127.0.0.1" ] || exit 86' in source
    assert 'case "$OLLAMA_HOST" in' in source
    assert '*[!0-9]*) exit 86' in source


def test_external_gateway_roles_are_not_blanket_network_sandboxed() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    compatibility = source.split('compatibility-gateway)', 1)[1].split('gateway)', 1)[0]
    gateway = source.split('gateway)', 1)[1].split('*)', 1)[0]
    assert "run_loopback_only" not in compatibility
    assert "run_loopback_only" not in gateway


def _cell_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    cell = tmp_path / "cell"
    release = tmp_path / "release"
    profile = tmp_path / "profile"
    (release / "surfaces/python-runtime/bin").mkdir(parents=True)
    (release / "surfaces/model-runtime").mkdir(parents=True)
    (profile / "primary").mkdir(parents=True)
    (cell / "runtime-volume").mkdir(parents=True)
    (cell / "current-release").symlink_to(release, target_is_directory=True)
    (cell / "current-profile").symlink_to(profile, target_is_directory=True)
    python = release / "surfaces/python-runtime/bin/python"
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o700)
    (release / "runtime-manifest.json").write_text('{}\n', encoding="utf-8")
    return cell, release, profile


def test_model_role_rejects_non_loopback_bind_before_exec(tmp_path: Path) -> None:
    cell, release, _ = _cell_fixture(tmp_path)
    marker = tmp_path / "model-executed"
    model = release / "surfaces/model-runtime/ollama"
    model.write_text(f"#!/bin/sh\n/usr/bin/touch {marker!s}\n", encoding="utf-8")
    model.chmod(0o700)
    environment = {
        **os.environ,
        "IK_CELL_ROOT": str(cell),
        "IK_RELEASE_IMAGE": str(cell / "release-store.sparsebundle"),
        "IK_RELEASE_MOUNT": str(cell / "runtime-volume"),
        "IK_CELL_SERVICE_ROLE": "model",
        "OLLAMA_HOST": "0.0.0.0:18421",
    }

    result = subprocess.run((str(SCRIPT),), env=environment, check=False)

    assert result.returncode == 86
    assert not marker.exists()


def test_router_role_rejects_non_loopback_bind_before_exec(tmp_path: Path) -> None:
    cell, _, _ = _cell_fixture(tmp_path)
    environment = {
        **os.environ,
        "IK_CELL_ROOT": str(cell),
        "IK_RELEASE_IMAGE": str(cell / "release-store.sparsebundle"),
        "IK_RELEASE_MOUNT": str(cell / "runtime-volume"),
        "IK_CELL_SERVICE_ROLE": "router",
        "HERMES_HOME": str(cell / "current-profile/primary"),
        "IK_SERVICE_HOST": "0.0.0.0",
        "IK_SERVICE_PORT": "18423",
    }

    result = subprocess.run((str(SCRIPT),), env=environment, check=False)

    assert result.returncode == 86
