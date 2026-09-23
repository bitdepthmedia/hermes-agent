"""Reject releases whose agent and tool modules cannot load together."""

from pathlib import Path
import subprocess
import tempfile

from .models import LifecycleBlockedError


def validate_runtime_imports(python: Path, source: Path) -> None:
    source = Path(source).resolve(strict=True)
    # Import against an empty profile: a live user's credentials and plugins
    # must not mask a missing dependency or incompatible core module.
    with tempfile.TemporaryDirectory(prefix="hermes-runtime-preflight-") as home:
        env = {
            "HOME": home,
            "HERMES_HOME": home,
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        sqlite_dir = Path(python).parent.parent / "lib/sqlite-safe"
        if sqlite_dir.is_dir():
            env["LD_LIBRARY_PATH"] = str(sqlite_dir)
        try:
            result = subprocess.run(
                [str(python), "-I", "-B", "-c",
                 "import sys; sys.path.insert(0, sys.argv[1]); "
                 "import agent.tool_executor, run_agent, model_tools, "
                 "gateway.run, hermes_cli.web_server",
                 str(source)],
                cwd=home, env=env, capture_output=True, timeout=90,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LifecycleBlockedError(
                "runtime_import_failed", "runtime import preflight could not complete"
            ) from exc
        if result.returncode:
            raise LifecycleBlockedError(
                "runtime_import_failed", "runtime import preflight failed"
            )
