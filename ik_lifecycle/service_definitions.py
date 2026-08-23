"""Deterministic, cell-isolated service definition construction."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import plistlib
import re

from .models import LifecycleBlockedError


@dataclass(frozen=True)
class CellServiceSpec:
    cell_id: str
    cell_root: Path
    service_label: str
    account: str
    gateway_port: int
    model_port: int
    release_image: Path
    release_mount: Path


@dataclass(frozen=True)
class RenderedServiceDefinitions:
    launchd_plist: Path
    systemd_unit: Path
    model_launchd_plist: Path
    model_systemd_unit: Path
    manifest_path: Path
    manifest: dict[str, object]


@dataclass(frozen=True)
class ErnieServiceTopologySpec:
    cell_root: Path
    service_label: str
    account: str
    model_port: int
    router_port: int
    fast_port: int
    primary_port: int
    compatibility_gateway_port: int
    release_image: Path
    release_mount: Path


@dataclass(frozen=True)
class RenderedErnieServiceTopology:
    launchd_plists: dict[str, Path]
    manifest_path: Path
    manifest: dict[str, object]


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_cell_service_definitions(
    spec: CellServiceSpec,
    output_root: Path,
    *,
    forbidden_roots: tuple[Path, ...] = (),
) -> RenderedServiceDefinitions:
    token = re.compile(r"[a-z][a-z0-9-]{1,31}")
    if (
        not token.fullmatch(spec.cell_id)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", spec.service_label)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", spec.account)
        or not 1024 <= spec.gateway_port <= 65535
        or not 1024 <= spec.model_port <= 65535
        or spec.gateway_port == spec.model_port
    ):
        raise LifecycleBlockedError("service_spec_invalid", "cell service specification is invalid")
    cell_root = Path(spec.cell_root).resolve(strict=False)
    release_image = Path(spec.release_image).resolve(strict=False)
    release_mount = Path(spec.release_mount).resolve(strict=False)
    output = Path(output_root).resolve(strict=False)
    for forbidden in forbidden_roots:
        root = Path(forbidden).resolve(strict=False)
        if _within(cell_root, root) or _within(root, cell_root):
            raise LifecycleBlockedError("service_cell_root_forbidden", "cell root overlaps a mutable checkout")
    if not _within(release_image, cell_root) or not _within(release_mount, cell_root):
        raise LifecycleBlockedError("service_release_store_invalid", "release image and mount must remain inside the cell root")
    if output.exists() or output.is_symlink():
        raise LifecycleBlockedError("service_output_exists", "service definition output must be new")
    output.mkdir(parents=True, mode=0o700)
    release_source = cell_root / "current-release/source"
    profile = cell_root / "current-profile"
    executable = cell_root / "bin/ik-cell-service"
    environment = {
        "HERMES_HOME": str(profile),
        "HERMES_WEB_DIST": str(cell_root / "current-release/surfaces/built-assets/dashboard-web-dist"),
        "IK_CELL_ROOT": str(cell_root),
        "IK_CELL_ID": spec.cell_id,
        "IK_GATEWAY_PORT": str(spec.gateway_port),
        "IK_MODEL_BASE_URL": f"http://127.0.0.1:{spec.model_port}/v1",
        "IK_ROUTER_CONFIG": str(cell_root / "current-release/config/router.json"),
        "IK_RELEASE_IMAGE": str(release_image),
        "IK_RELEASE_MOUNT": str(release_mount),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    launchd = {
        "Label": spec.service_label,
        "ProgramArguments": [str(executable)],
        "WorkingDirectory": str(cell_root),
        "EnvironmentVariables": environment,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Interactive",
    }
    launchd_path = output / f"{spec.service_label}.plist"
    launchd_path.write_bytes(plistlib.dumps(launchd, sort_keys=True))
    systemd_path = output / f"{spec.service_label}.service"
    systemd_path.write_text(
        "\n".join(
            (
                "[Unit]",
                f"Description=Hermes {spec.cell_id} isolated cell",
                "After=network.target",
                "",
                "[Service]",
                "Type=simple",
                f"User={spec.account}",
                f"WorkingDirectory={cell_root}",
                f"Environment=HERMES_HOME={profile}",
                f"Environment=HERMES_WEB_DIST={cell_root / 'current-release/surfaces/built-assets/dashboard-web-dist'}",
                f"Environment=IK_CELL_ROOT={cell_root}",
                f"Environment=IK_CELL_ID={spec.cell_id}",
                f"Environment=IK_GATEWAY_PORT={spec.gateway_port}",
                f"Environment=IK_MODEL_BASE_URL=http://127.0.0.1:{spec.model_port}/v1",
                f"Environment=IK_ROUTER_CONFIG={cell_root / 'current-release/config/router.json'}",
                f"Environment=IK_RELEASE_IMAGE={release_image}",
                f"Environment=IK_RELEASE_MOUNT={release_mount}",
                "Environment=PYTHONDONTWRITEBYTECODE=1",
                f"ExecStart={executable}",
                "Restart=on-failure",
                "RestartSec=5",
                "NoNewPrivileges=true",
                "PrivateTmp=true",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            )
        ),
        encoding="utf-8",
    )
    model_label = f"{spec.service_label}.model"
    model_executable = cell_root / "current-release/surfaces/model-runtime/ollama"
    model_environment = {
        "HOME": str(cell_root / "model-home"),
        "OLLAMA_HOST": f"127.0.0.1:{spec.model_port}",
        "OLLAMA_MODELS": str(cell_root / "model-store"),
        "NO_PROXY": "127.0.0.1,localhost",
    }
    model_launchd_path = output / f"{model_label}.plist"
    model_launchd_path.write_bytes(
        plistlib.dumps(
            {
                "Label": model_label,
                "ProgramArguments": [str(model_executable), "serve"],
                "WorkingDirectory": str(cell_root),
                "EnvironmentVariables": model_environment,
                "RunAtLoad": True,
                "KeepAlive": {"SuccessfulExit": False},
                "ProcessType": "Interactive",
            },
            sort_keys=True,
        )
    )
    model_systemd_path = output / f"{model_label}.service"
    model_systemd_path.write_text(
        "\n".join(
            (
                "[Unit]",
                f"Description=Hermes {spec.cell_id} model worker",
                "After=network.target",
                "",
                "[Service]",
                "Type=simple",
                f"User={spec.account}",
                f"WorkingDirectory={cell_root}",
                f"Environment=HOME={cell_root / 'model-home'}",
                f"Environment=OLLAMA_HOST=127.0.0.1:{spec.model_port}",
                f"Environment=OLLAMA_MODELS={cell_root / 'model-store'}",
                "Environment=NO_PROXY=127.0.0.1,localhost",
                f"ExecStart={model_executable} serve",
                "Restart=on-failure",
                "RestartSec=5",
                "NoNewPrivileges=true",
                "PrivateTmp=true",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            )
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_id": "ik.hermes.cell-service-definitions.v1",
        "status": "CLEAR_EXACT_SERVICE_DEFINITIONS",
        "cell_id": spec.cell_id,
        "cell_root_sha256": hashlib.sha256(str(cell_root).encode()).hexdigest(),
        "release_image_path_sha256": hashlib.sha256(str(release_image).encode()).hexdigest(),
        "release_mount_path_sha256": hashlib.sha256(str(release_mount).encode()).hexdigest(),
        "service_label": spec.service_label,
        "gateway_port": spec.gateway_port,
        "model_port": spec.model_port,
        "launchd_sha256": _sha(launchd_path),
        "systemd_sha256": _sha(systemd_path),
        "model_launchd_sha256": _sha(model_launchd_path),
        "model_systemd_sha256": _sha(model_systemd_path),
    }
    manifest_path = output / "service-manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return RenderedServiceDefinitions(
        launchd_path, systemd_path, model_launchd_path, model_systemd_path, manifest_path, manifest
    )


def render_ernie_service_topology(
    spec: ErnieServiceTopologySpec,
    output_root: Path,
    *,
    forbidden_roots: tuple[Path, ...] = (),
) -> RenderedErnieServiceTopology:
    """Render the complete Ernie cell as one ordered, rollback-safe service group."""

    cell_root = Path(spec.cell_root).resolve(strict=False)
    output = Path(output_root).resolve(strict=False)
    release_image = Path(spec.release_image).resolve(strict=False)
    release_mount = Path(spec.release_mount).resolve(strict=False)
    ports = (
        spec.model_port,
        spec.router_port,
        spec.fast_port,
        spec.primary_port,
        spec.compatibility_gateway_port,
    )
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+", spec.service_label)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+", spec.account)
        or len(set(ports)) != len(ports)
        or any(not 1024 <= port <= 65535 for port in ports)
        or not _within(release_image, cell_root)
        or not _within(release_mount, cell_root)
    ):
        raise LifecycleBlockedError("ernie_service_spec_invalid", "Ernie service topology specification is invalid")
    for forbidden in forbidden_roots:
        root = Path(forbidden).resolve(strict=False)
        if _within(cell_root, root) or _within(root, cell_root):
            raise LifecycleBlockedError("service_cell_root_forbidden", "cell root overlaps a mutable checkout")
    if output.exists() or output.is_symlink():
        raise LifecycleBlockedError("service_output_exists", "service definition output must be new")
    output.mkdir(parents=True, mode=0o700)
    executable = cell_root / "bin/ik-cell-service"
    common = {
        "IK_CELL_ROOT": str(cell_root),
        "IK_CELL_ID": "ernie",
        "IK_RELEASE_IMAGE": str(release_image),
        "IK_RELEASE_MOUNT": str(release_mount),
        "PYTHONDONTWRITEBYTECODE": "1",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    roles: tuple[tuple[str, str, dict[str, str]], ...] = (
        (
            "model",
            f"{spec.service_label}.model",
            {"IK_CELL_SERVICE_ROLE": "model", "HOME": str(cell_root / "model-home"), "OLLAMA_HOST": f"127.0.0.1:{spec.model_port}", "OLLAMA_MODELS": str(cell_root / "model-store")},
        ),
        (
            "router",
            f"{spec.service_label}.router",
            {"IK_CELL_SERVICE_ROLE": "router", "HERMES_HOME": str(cell_root / "current-profile/primary"), "IK_ROUTER_CONFIG": str(cell_root / "current-release/config/router.json"), "IK_CELL_CREDENTIAL_FILE": str(cell_root / "current-profile/router/.env"), "OLLAMA_BASE_URL": f"http://127.0.0.1:{spec.model_port}", "IK_SERVICE_HOST": "127.0.0.1", "IK_SERVICE_PORT": str(spec.router_port)},
        ),
        (
            "fast",
            f"{spec.service_label}.fast",
            {"IK_CELL_SERVICE_ROLE": "gateway", "HERMES_HOME": str(cell_root / "current-profile/fast"), "IK_ROUTER_CONFIG": str(cell_root / "current-release/config/router.json"), "IK_MODEL_BASE_URL": f"http://127.0.0.1:{spec.router_port}/v1", "API_SERVER_HOST": "127.0.0.1", "API_SERVER_PORT": str(spec.fast_port), "API_SERVER_ENABLED": "true"},
        ),
        (
            "primary",
            spec.service_label,
            {"IK_CELL_SERVICE_ROLE": "gateway", "HERMES_HOME": str(cell_root / "current-profile/primary"), "IK_ROUTER_CONFIG": str(cell_root / "current-release/config/router.json"), "IK_MODEL_BASE_URL": f"http://127.0.0.1:{spec.router_port}/v1", "API_SERVER_HOST": "127.0.0.1", "API_SERVER_PORT": str(spec.primary_port), "API_SERVER_ENABLED": "true"},
        ),
        (
            "compatibility-gateway",
            f"{spec.service_label}.compatibility-gateway",
            {"IK_CELL_SERVICE_ROLE": "compatibility-gateway", "HERMES_HOME": str(cell_root / "current-profile/primary"), "IK_CELL_CREDENTIAL_FILE": str(cell_root / "current-profile/compatibility-gateway/.env"), "IK_CELL_SHARED_CREDENTIAL_FILE": str(cell_root / "current-profile/compatibility-gateway/shared-core.env"), "ERNIE_FAST_BASE_URL": f"http://127.0.0.1:{spec.fast_port}/v1", "ERNIE_OPERATOR_BASE_URL": f"http://127.0.0.1:{spec.primary_port}/v1", "IK_SERVICE_HOST": "127.0.0.1", "IK_SERVICE_PORT": str(spec.compatibility_gateway_port)},
        ),
    )
    launchd_plists: dict[str, Path] = {}
    for role, label, environment in roles:
        path = output / f"{label}.plist"
        path.write_bytes(
            plistlib.dumps(
                {
                    "Label": label,
                    "ProgramArguments": [str(executable)],
                    "WorkingDirectory": str(cell_root),
                    "EnvironmentVariables": {**common, **environment},
                    "RunAtLoad": True,
                    "KeepAlive": {"SuccessfulExit": False},
                    "ProcessType": "Interactive",
                },
                sort_keys=True,
            )
        )
        launchd_plists[role] = path
    start_order = [role for role, _, _ in roles]
    manifest = {
        "schema_id": "ik.hermes.ernie-service-topology.v1",
        "status": "CLEAR_EXACT_ERNIE_TOPOLOGY",
        "cell_id": "ernie",
        "service_label": spec.service_label,
        "start_order": start_order,
        "stop_order": list(reversed(start_order)),
        "ports": {"model": spec.model_port, "router": spec.router_port, "fast": spec.fast_port, "primary": spec.primary_port, "compatibility-gateway": spec.compatibility_gateway_port},
        "launchd_sha256": {role: _sha(path) for role, path in launchd_plists.items()},
        "release_image_path_sha256": hashlib.sha256(str(release_image).encode()).hexdigest(),
        "release_mount_path_sha256": hashlib.sha256(str(release_mount).encode()).hexdigest(),
    }
    manifest_path = output / "service-manifest.json"
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return RenderedErnieServiceTopology(launchd_plists, manifest_path, manifest)
