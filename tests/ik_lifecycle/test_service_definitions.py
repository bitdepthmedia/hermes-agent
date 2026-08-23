from __future__ import annotations

from pathlib import Path
import plistlib

import pytest

from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.service_definitions import CellServiceSpec, render_cell_service_definitions


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
        ),
        tmp_path / "definitions",
    )

    launchd = plistlib.loads(result.launchd_plist.read_bytes())
    assert launchd["ProgramArguments"] == [str(cell / "current-release/source/scripts/ik-cell-service")]
    assert launchd["WorkingDirectory"] == str(cell / "current-release/source")
    assert launchd["EnvironmentVariables"]["HERMES_HOME"] == str(cell / "current-profile")
    assert launchd["EnvironmentVariables"]["IK_CELL_ROOT"] == str(cell)
    assert launchd["EnvironmentVariables"]["IK_CELL_ID"] == "ernie"
    assert launchd["EnvironmentVariables"]["IK_MODEL_BASE_URL"] == "http://127.0.0.1:18422/v1"
    systemd = result.systemd_unit.read_text(encoding="utf-8")
    assert f"ExecStart={cell}/current-release/source/scripts/ik-cell-service" in systemd
    assert f"Environment=HERMES_HOME={cell}/current-profile" in systemd
    assert "Restart=on-failure" in systemd
    model_launchd = plistlib.loads(result.model_launchd_plist.read_bytes())
    assert model_launchd["Label"] == "com.ik.hermes-ernie-v2.model"
    assert model_launchd["ProgramArguments"] == [str(cell / "current-release/surfaces/model-runtime/ollama"), "serve"]
    assert model_launchd["EnvironmentVariables"]["OLLAMA_HOST"] == "127.0.0.1:18422"
    assert model_launchd["EnvironmentVariables"]["OLLAMA_MODELS"] == str(cell / "model-store")
    assert "ExecStart=" + str(cell / "current-release/surfaces/model-runtime/ollama") + " serve" in result.model_systemd_unit.read_text()
    assert result.manifest["status"] == "CLEAR_EXACT_SERVICE_DEFINITIONS"


def test_service_definition_rejects_mutable_checkout_or_bad_identity(tmp_path: Path) -> None:
    for cell_id, cell_root in (("Ernie", tmp_path / "cell"), ("ernie", Path.cwd())):
        with pytest.raises(LifecycleBlockedError):
            render_cell_service_definitions(
                CellServiceSpec(cell_id, cell_root, "com.ik.hermes", "react", 18421, 18422),
                tmp_path / f"out-{cell_id}",
                forbidden_roots=(Path.cwd(),),
            )
