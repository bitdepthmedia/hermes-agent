import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest


@pytest.mark.spawns_gateway_lookalike
def test_host_health_rejects_stale_or_wrong_process_evidence(tmp_path):
    assert importlib.util.find_spec("ik_lifecycle.host_release"), (
        "promotion needs PID-bound host evidence"
    )
    from ik_lifecycle.host_release import observe_host

    source = tmp_path / "release/source"
    source.mkdir(parents=True)
    profile = tmp_path / "profile"
    (profile / "state").mkdir(parents=True)
    python = source.parent / "surfaces/python-runtime/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    package = source / "hermes_cli"
    package.mkdir()
    (package / "__init__.py").touch()
    (package / "main.py").write_text("import time; time.sleep(30)\n")
    process = subprocess.Popen(
        [sys.executable, "-m", "hermes_cli.main", "gateway", "run"],
        cwd=source,
        env={**os.environ, "PYTHONPATH": str(source), "HERMES_HOME": str(profile)},
    )

    class Service:
        def pids(self):
            return {process.pid}

    started = time.time() - 1
    spec = {"profiles": [{"path": ".", "platforms": ["telegram"]}], "ports": []}

    def evidence(pid):
        (profile / "gateway_state.json").write_text(
            json.dumps({
                "pid": pid,
                "gateway_state": "running",
                "platforms": {"telegram": {"state": "connected"}},
            })
        )
        (profile / "state/gateway.heartbeat").write_text(
            json.dumps({"pid": pid, "loop_tick_socket": True})
        )

    try:
        evidence(process.pid)
        assert observe_host(
            source.parent, profile, started, set(), spec, Service()
        ) == {process.pid}
        python.unlink()
        python.symlink_to("/usr/bin/false")
        assert (
            observe_host(source.parent, profile, started, set(), spec, Service())
            is None
        )
        python.unlink()
        python.symlink_to(sys.executable)
        assert (
            observe_host(
                source.parent, profile, time.time() + 1, set(), spec, Service()
            )
            is None
        )
        assert (
            observe_host(
                source.parent, profile, started, {process.pid}, spec, Service()
            )
            is None
        )
        evidence(process.pid + 100000)
        assert (
            observe_host(source.parent, profile, started, set(), spec, Service())
            is None
        )
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_loaded_launchd_bindings_reject_stale_definition():
    from ik_lifecycle.host_release import validate_loaded_launchd
    from ik_lifecycle.models import LifecycleBlockedError

    unit = {
        "program": "/release/start",
        "workdir": "/release/source",
        "profile": "/profile",
    }
    raw = "program = /release/start\nworking directory = /release/source\nenvironment = {\n HERMES_HOME => /profile\n}\n"
    validate_loaded_launchd(raw, unit)
    with pytest.raises(LifecycleBlockedError):
        validate_loaded_launchd(raw.replace("/release/start", "/old/start"), unit)
    with pytest.raises(LifecycleBlockedError):
        validate_loaded_launchd(raw.replace("/profile", "/old/profile"), unit)


def test_candidate_service_bindings_do_not_reuse_old_launcher():
    from ik_lifecycle.host_release import service_binding

    unit = {
        "name": "gateway",
        "program": "/old/start",
        "candidate": {"program": "/new/start"},
    }
    assert service_binding(unit, False)["program"] == "/old/start"
    assert service_binding(unit, True)["program"] == "/new/start"
