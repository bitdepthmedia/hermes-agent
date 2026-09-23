from pathlib import Path
import sys

import pytest

from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.runtime_imports import validate_runtime_imports


def test_runtime_imports_reject_mixed_display_and_executor(tmp_path: Path):
    for package in ("agent", "gateway", "hermes_cli"):
        (tmp_path / package).mkdir()
        (tmp_path / package / "__init__.py").touch()
    for module in ("run_agent.py", "model_tools.py", "gateway/run.py", "hermes_cli/web_server.py"):
        (tmp_path / module).touch()
    (tmp_path / "agent/display.py").write_text("", encoding="utf-8")
    (tmp_path / "agent/tool_executor.py").write_text(
        "from agent.display import redact_tool_args_for_display\n", encoding="utf-8"
    )
    with pytest.raises(LifecycleBlockedError, match="runtime import"):
        validate_runtime_imports(Path(sys.executable), tmp_path)

    (tmp_path / "agent/display.py").write_text(
        "def redact_tool_args_for_display(name, args): return args\n", encoding="utf-8"
    )
    validate_runtime_imports(Path(sys.executable), tmp_path)


def test_runtime_imports_do_not_use_operator_profile_or_credentials(tmp_path: Path, monkeypatch):
    for package in ("agent", "gateway", "hermes_cli"):
        (tmp_path / package).mkdir()
        (tmp_path / package / "__init__.py").touch()
    for module in ("agent/tool_executor.py", "model_tools.py", "gateway/run.py", "hermes_cli/web_server.py"):
        (tmp_path / module).touch()
    (tmp_path / "run_agent.py").write_text(
        "import os, pathlib\n"
        "assert 'OPENAI_API_KEY' not in os.environ\n"
        "assert os.environ['HERMES_HOME'] != 'operator-profile'\n"
        "assert pathlib.Path(os.environ['HERMES_HOME']).is_dir()\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("HERMES_HOME", "operator-profile")
    validate_runtime_imports(Path(sys.executable), tmp_path)
