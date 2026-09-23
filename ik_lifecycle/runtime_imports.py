"""Reject releases whose agent and tool modules cannot load together."""

from pathlib import Path
import subprocess
import tempfile

from .models import LifecycleBlockedError


def _run_probe(python: Path, source: Path, code: str, kind: str) -> None:
    source = Path(source).resolve(strict=True)
    # Import against an empty profile: a live user's credentials and plugins
    # must not mask a missing dependency or incompatible core module.
    with tempfile.TemporaryDirectory(prefix="hermes-runtime-preflight-") as home:
        env = {
            "HOME": home,
            "HERMES_HOME": home,
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "TERMINAL_ENV": "local",
            "TERMINAL_CWD": home,
        }
        sqlite_dir = Path(python).parent.parent / "lib/sqlite-safe"
        if sqlite_dir.is_dir():
            env["LD_LIBRARY_PATH"] = str(sqlite_dir)
        try:
            result = subprocess.run(
                [
                    str(python),
                    "-I",
                    "-B",
                    "-c",
                    "import sys; sys.path.insert(0, sys.argv[1]); " + code,
                    str(source),
                ],
                cwd=home,
                env=env,
                capture_output=True,
                timeout=90,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LifecycleBlockedError(
                f"runtime_{kind}_failed", f"runtime {kind} preflight could not complete"
            ) from exc
        if result.returncode:
            raise LifecycleBlockedError(
                f"runtime_{kind}_failed", f"runtime {kind} preflight failed"
            )


def validate_runtime_imports(python: Path, source: Path) -> None:
    _run_probe(
        python,
        source,
        "import agent.tool_executor, run_agent, model_tools, gateway.run, hermes_cli.web_server",
        "import",
    )


def validate_runtime_tools(python: Path, source: Path) -> None:
    _run_probe(
        python,
        source,
        """
import json, pathlib
from model_tools import get_tool_definitions, handle_function_call
names = {d['function']['name'] for d in get_tool_definitions(enabled_toolsets=['terminal', 'file'])}
assert {'terminal', 'read_file'} <= names
result = json.loads(handle_function_call('terminal', {'command': 'printf HERMES_SEAL_TERMINAL_OK'}, task_id='release-preflight'))
assert not result.get('error') and 'HERMES_SEAL_TERMINAL_OK' in str(result)
path = pathlib.Path.cwd() / 'probe.txt'
path.write_text('HERMES_SEAL_FILE_OK', encoding='utf-8')
result = json.loads(handle_function_call('read_file', {'path': str(path)}, task_id='release-preflight'))
assert not result.get('error') and 'HERMES_SEAL_FILE_OK' in str(result)
""",
        "tool",
    )
