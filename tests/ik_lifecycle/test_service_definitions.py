from __future__ import annotations

from pathlib import Path
import plistlib

import pytest

from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.service_definitions import (
    BertServiceTopologySpec,
    CellServiceSpec,
    ErnieServiceTopologySpec,
    render_cell_service_definitions,
    render_bert_service_topology,
    render_ernie_service_topology,
)


def test_bert_topology_uses_one_stable_cell_launcher_without_local_model_service(tmp_path: Path) -> None:
    cell = tmp_path / "bert-cell"
    profile = tmp_path / "bert-profile"
    result = render_bert_service_topology(
        BertServiceTopologySpec(
            cell_root=cell,
            profile_root=profile,
            writable_paths=(tmp_path / "nate-state" / "bert",),
            account="bert",
            gateway_unit="hermes-gateway.service",
            dashboard_unit="hermes-dashboard-bert.service",
            dashboard_port=7611,
            runtime_manifest_sha256="a" * 64,
            runtime_verifier_sha256="c" * 64,
        ),
        tmp_path / "bert-definitions",
        forbidden_roots=(Path.cwd(),),
    )

    gateway = result.systemd_units["gateway"].read_text(encoding="utf-8")
    dashboard = result.systemd_units["dashboard"].read_text(encoding="utf-8")
    launcher = cell / "bin/ik-bert-cell-service"
    assert f"ExecStart={launcher}" in gateway
    assert f"ExecStart={launcher}" in dashboard
    assert f"Environment=HERMES_HOME={cell}/current-profile" in gateway
    assert "Environment=IK_RUNTIME_MANIFEST_SHA256=" + "a" * 64 in gateway
    assert "Environment=IK_RUNTIME_VERIFIER_SHA256=" + "c" * 64 in gateway
    assert "Environment=IK_CELL_SERVICE_ROLE=gateway" in gateway
    assert "Environment=IK_CELL_SERVICE_ROLE=dashboard" in dashboard
    assert "--host 127.0.0.1 --port 7611 --no-open" not in dashboard
    assert "model" not in result.systemd_units
    assert f"ReadWritePaths={profile}" in gateway
    assert f"ReadWritePaths={tmp_path / 'nate-state' / 'bert'}" in gateway
    assert f"ReadWritePaths={profile} {cell}" not in gateway
    assert "TimeoutStopSec=210" in gateway
    assert "TimeoutStopSec=210" in dashboard
    assert result.manifest["additional_writable_path_count"] == 1
    assert result.manifest["status"] == "CLEAR_EXACT_BERT_TOPOLOGY"
    assert result.manifest["start_order"] == ["gateway", "dashboard"]
    assert result.manifest["stop_order"] == ["dashboard", "gateway"]


def test_bert_topology_allows_cell_local_profile_and_rejects_unsafe_unit_paths(tmp_path: Path) -> None:
    cell = tmp_path / "bert-cell"
    result = render_bert_service_topology(
        BertServiceTopologySpec(
            cell_root=cell,
            profile_root=cell / "profiles/live",
            account="bert",
            gateway_unit="hermes-gateway.service",
            dashboard_unit="hermes-dashboard-bert.service",
            dashboard_port=7611,
            runtime_manifest_sha256="b" * 64,
            runtime_verifier_sha256="c" * 64,
        ),
        tmp_path / "definitions",
    )
    assert result.manifest["status"] == "CLEAR_EXACT_BERT_TOPOLOGY"

    with pytest.raises(LifecycleBlockedError, match="invalid"):
        render_bert_service_topology(
            BertServiceTopologySpec(
                cell_root=tmp_path / "unsafe cell",
                profile_root=tmp_path / "profile",
                account="bert",
                gateway_unit="hermes-gateway.service",
                dashboard_unit="hermes-dashboard-bert.service",
                dashboard_port=7611,
                runtime_manifest_sha256="b" * 64,
                runtime_verifier_sha256="c" * 64,
            ),
            tmp_path / "unsafe-definitions",
        )

    with pytest.raises(LifecycleBlockedError, match="writable"):
        render_bert_service_topology(
            BertServiceTopologySpec(
                cell_root=cell,
                profile_root=tmp_path / "profile",
                writable_paths=(Path("/"),),
                account="bert",
                gateway_unit="hermes-gateway.service",
                dashboard_unit="hermes-dashboard-bert.service",
                dashboard_port=7611,
                runtime_manifest_sha256="b" * 64,
                runtime_verifier_sha256="c" * 64,
            ),
            tmp_path / "unsafe-writable-definitions",
        )


def test_bert_topology_preserves_remote_posix_paths_on_macos(tmp_path: Path) -> None:
    result = render_bert_service_topology(
        BertServiceTopologySpec(
            cell_root=Path("/home/bert/.hermes-cells/bert"),
            profile_root=Path("/home/bert/.hermes"),
            writable_paths=(Path("/home/bert/.nate-os/state/bert"),),
            account="bert",
            gateway_unit="hermes-gateway.service",
            dashboard_unit="hermes-dashboard-bert.service",
            dashboard_port=7611,
            runtime_manifest_sha256="a" * 64,
            runtime_verifier_sha256="b" * 64,
        ),
        tmp_path / "remote-definitions",
    )

    unit = result.systemd_units["gateway"].read_text(encoding="utf-8")
    assert "WorkingDirectory=/home/bert/.hermes-cells/bert" in unit
    assert "ReadWritePaths=/home/bert/.hermes" in unit
    assert "ReadWritePaths=/home/bert/.nate-os/state/bert" in unit
    assert "/System/Volumes/Data/home" not in unit


def test_renders_exact_launchd_and_systemd_definitions_from_cell_pointers(tmp_path: Path) -> None:
    cell = tmp_path / "cell"
    result = render_cell_service_definitions(
        CellServiceSpec(
            cell_id="ernie",
            cell_root=cell,
            service_label="com.ik.hermes-ernie-v2",
            account="react",
            gateway_port=18421,
            model_port=18422,
            release_image=cell / "release-store.sparsebundle",
            release_mount=cell / "runtime-volume",
        ),
        tmp_path / "definitions",
    )

    launchd = plistlib.loads(result.launchd_plist.read_bytes())
    assert launchd["ProgramArguments"] == [str(cell / "bin/ik-cell-service")]
    assert launchd["WorkingDirectory"] == str(cell)
    assert launchd["EnvironmentVariables"]["HERMES_HOME"] == str(cell / "current-profile")
    assert launchd["EnvironmentVariables"]["IK_CELL_ROOT"] == str(cell)
    assert launchd["EnvironmentVariables"]["IK_CELL_ID"] == "ernie"
    assert launchd["EnvironmentVariables"]["IK_MODEL_BASE_URL"] == "http://127.0.0.1:18422/v1"
    assert launchd["EnvironmentVariables"]["IK_ROUTER_CONFIG"] == str(cell / "current-release/config/router.json")
    assert launchd["EnvironmentVariables"]["HERMES_WEB_DIST"] == str(
        cell / "current-release/surfaces/built-assets/dashboard-web-dist"
    )
    assert launchd["EnvironmentVariables"]["IK_RELEASE_IMAGE"] == str(cell / "release-store.sparsebundle")
    assert launchd["EnvironmentVariables"]["IK_RELEASE_MOUNT"] == str(cell / "runtime-volume")
    systemd = result.systemd_unit.read_text(encoding="utf-8")
    assert f"ExecStart={cell}/bin/ik-cell-service" in systemd
    assert f"Environment=HERMES_HOME={cell}/current-profile" in systemd
    assert f"Environment=IK_ROUTER_CONFIG={cell}/current-release/config/router.json" in systemd
    assert f"Environment=HERMES_WEB_DIST={cell}/current-release/surfaces/built-assets/dashboard-web-dist" in systemd
    assert "Restart=on-failure" in systemd
    model_launchd = plistlib.loads(result.model_launchd_plist.read_bytes())
    assert model_launchd["Label"] == "com.ik.hermes-ernie-v2.model"
    assert model_launchd["ProgramArguments"] == [str(cell / "current-release/surfaces/model-runtime/ollama"), "serve"]
    assert model_launchd["EnvironmentVariables"]["OLLAMA_HOST"] == "127.0.0.1:18422"
    assert model_launchd["EnvironmentVariables"]["OLLAMA_MODELS"] == str(cell / "model-store")
    assert "ExecStart=" + str(cell / "current-release/surfaces/model-runtime/ollama") + " serve" in result.model_systemd_unit.read_text()
    assert result.manifest["status"] == "CLEAR_EXACT_SERVICE_DEFINITIONS"
    assert result.manifest["release_image_path_sha256"]
    assert result.manifest["release_mount_path_sha256"]


def test_service_definition_rejects_mutable_checkout_or_bad_identity(tmp_path: Path) -> None:
    for cell_id, cell_root in (("Ernie", tmp_path / "cell"), ("ernie", Path.cwd())):
        with pytest.raises(LifecycleBlockedError):
            render_cell_service_definitions(
                CellServiceSpec(cell_id, cell_root, "com.ik.hermes", "react", 18421, 18422, cell_root / "store.sparsebundle", cell_root / "runtime"),
                tmp_path / f"out-{cell_id}",
                forbidden_roots=(Path.cwd(),),
            )


def test_service_definitions_reject_same_runtime_image_and_mountpoint(tmp_path: Path) -> None:
    cell = tmp_path / "cell"

    with pytest.raises(LifecycleBlockedError, match="release image and mountpoint must be distinct"):
        render_ernie_service_topology(
            ErnieServiceTopologySpec(
                cell_root=cell,
                service_label="com.ik.hermes-ernie-v2",
                account="react",
                model_port=18421,
                router_port=18423,
                fast_port=18424,
                primary_port=18425,
                compatibility_gateway_port=18426,
                release_image=cell / "runtime-volume",
                release_mount=cell / "runtime-volume",
            ),
            tmp_path / "ernie-definitions",
        )


def test_ernie_topology_defines_model_router_two_hermes_profiles_and_compat_gateway(tmp_path: Path) -> None:
    cell = tmp_path / "cell"
    result = render_ernie_service_topology(
        ErnieServiceTopologySpec(
            cell_root=cell,
            service_label="com.ik.hermes-ernie-v2",
            account="react",
            model_port=18421,
            router_port=18423,
            fast_port=18424,
            primary_port=18425,
            compatibility_gateway_port=18426,
            release_image=cell / "release-store.sparsebundle",
            release_mount=cell / "runtime-volume",
        ),
        tmp_path / "ernie-definitions",
    )

    assert tuple(result.launchd_plists) == (
        "model",
        "router",
        "fast",
        "primary",
        "compatibility-gateway",
    )
    documents = {name: plistlib.loads(path.read_bytes()) for name, path in result.launchd_plists.items()}
    assert documents["model"]["EnvironmentVariables"]["OLLAMA_HOST"] == "127.0.0.1:18421"
    assert documents["router"]["EnvironmentVariables"]["IK_CELL_SERVICE_ROLE"] == "router"
    assert documents["router"]["EnvironmentVariables"]["OLLAMA_BASE_URL"] == "http://127.0.0.1:18421"
    assert documents["fast"]["EnvironmentVariables"]["HERMES_HOME"].endswith("current-profile/fast")
    assert documents["fast"]["EnvironmentVariables"]["IK_MODEL_BASE_URL"] == "http://127.0.0.1:18423/v1"
    assert documents["primary"]["EnvironmentVariables"]["HERMES_HOME"].endswith("current-profile/primary")
    assert documents["primary"]["EnvironmentVariables"]["IK_MODEL_BASE_URL"] == "http://127.0.0.1:18423/v1"
    assert documents["compatibility-gateway"]["EnvironmentVariables"]["IK_CELL_SERVICE_ROLE"] == "compatibility-gateway"
    assert result.manifest["start_order"] == ["model", "router", "fast", "primary", "compatibility-gateway"]
