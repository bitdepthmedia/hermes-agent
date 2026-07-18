import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "ik_profiles" / "hermes-ernie" / "cron"
WRAPPER = ROOT / "scripts" / "ik-ernie-daily-goal"


def checkout_root_for_common_git_dir(common_git_dir):
    return common_git_dir.parent


def common_git_dir(root):
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        text=True,
        capture_output=True,
        check=True,
    )
    return Path(result.stdout.strip())


def common_checkout_root(root):
    return checkout_root_for_common_git_dir(common_git_dir(root))


def repository_python_for_common_git_dir(common_git_dir):
    return checkout_root_for_common_git_dir(common_git_dir) / ".venv" / "bin" / "python"


def runtime_python_supports_cron(python):
    return subprocess.run(
        [str(python), "-c", "import croniter; from cron.jobs import compute_next_run"],
        text=True,
        capture_output=True,
        check=False,
    ).returncode == 0


def resolve_runtime_python():
    system = Path(sys.executable)
    if runtime_python_supports_cron(system):
        return system
    repository_python = repository_python_for_common_git_dir(common_git_dir(ROOT))
    if runtime_python_supports_cron(repository_python):
        return repository_python
    raise RuntimeError("no Python runtime with croniter is available for profile tests")


def test_runtime_python_resolution_works_from_linked_and_normal_checkouts():
    runtime_python = resolve_runtime_python()
    assert runtime_python_supports_cron(runtime_python)
    system_python = Path(sys.executable)
    if runtime_python_supports_cron(system_python):
        assert runtime_python == system_python
    else:
        common_root = common_checkout_root(ROOT)
        assert runtime_python == common_root / ".venv" / "bin" / "python"
        assert runtime_python.is_file()

    normal_common_git_dir = Path("/tmp/arbitrary-clone-name/.git")
    assert checkout_root_for_common_git_dir(normal_common_git_dir) == Path("/tmp/arbitrary-clone-name")
    assert repository_python_for_common_git_dir(normal_common_git_dir) == Path(
        "/tmp/arbitrary-clone-name/.venv/bin/python"
    )


def load(name):
    return json.loads((PROFILE / name).read_text())


def test_checkin_is_exact_time_direct_tool():
    job = load("daily-goal-checkin.json")
    assert job["id"] == "bcfc1f4e449e"
    assert job["schedule"] == {"kind": "cron", "expr": "5 9 * * *", "display": "5 9 * * *"}
    assert job["direct_tool"]["name"] == "daily_goal_coordinator"
    assert job["direct_tool"]["args"] == {"mode": "checkin", "dry_run": False}
    assert job["deliver"] is None


def test_watchdog_is_exact_time_and_reuses_coordinator():
    job = load("daily-goal-watchdog.json")
    assert job["id"] == "d41a1c0de160"
    assert job["schedule"]["expr"] == "0 16 * * *"
    assert job["direct_tool"]["name"] == "daily_goal_coordinator"
    assert job["direct_tool"]["args"] == {"mode": "watchdog", "dry_run": False}


def test_wrapper_is_executable():
    assert os.access(WRAPPER, os.X_OK)


def test_wrapper_declares_test_home_guard_before_temp_home_execution():
    source = WRAPPER.read_text()
    assert "IK_ERNIE_ALLOW_TEST_HOME" in source
    assert "--git-common-dir" in source


def seed_home(tmp_path, deliver="telegram:-123", timezone=""):
    home = tmp_path / "hermes-ernie"
    (home / "cron").mkdir(parents=True)
    (home / "config.yaml").write_text(f"timezone: {timezone}\n")
    job = {
        "id": "bcfc1f4e449e",
        "name": "ernie-telegram-daily-checkin",
        "deliver": deliver,
        "schedule": {"kind": "interval", "minutes": 1440},
        "last_status": "error",
    }
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [job]}) + "\n")
    return home


def run_wrapper(home, action, *, cwd=ROOT, extra_env=None):
    env = dict(os.environ)
    env["HERMES_HOME"] = str(home)
    env["IK_ERNIE_ALLOW_TEST_HOME"] = "1"
    env["IK_ERNIE_TEST_ROOT"] = str(home.parent)
    env["HERMES_PYTHON"] = str(resolve_runtime_python())
    env.update(extra_env or {})
    return subprocess.run(
        [str(WRAPPER), action],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def run_wrapper_with_test_root(test_root, action, home=None, python=None):
    env = dict(os.environ)
    env["IK_ERNIE_ALLOW_TEST_HOME"] = "1"
    env["IK_ERNIE_TEST_ROOT"] = str(test_root)
    env["HERMES_PYTHON"] = str(resolve_runtime_python() if python is None else python)
    if home is None:
        env.pop("HERMES_HOME", None)
    else:
        env["HERMES_HOME"] = str(home)
    return subprocess.run(
        [str(WRAPPER), action],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_deploy_preserves_delivery_sets_timezone_and_is_idempotent(tmp_path):
    home = seed_home(tmp_path)
    first = run_wrapper(home, "deploy")
    second = run_wrapper(home, "deploy")
    assert first.returncode == second.returncode == 0
    data = json.loads((home / "cron" / "jobs.json").read_text())
    by_id = {job["id"]: job for job in data["jobs"]}
    assert set(by_id) == {"bcfc1f4e449e", "d41a1c0de160"}
    assert by_id["bcfc1f4e449e"]["deliver"] == "telegram:-123"
    assert by_id["d41a1c0de160"]["deliver"] == "telegram:-123"
    assert by_id["bcfc1f4e449e"]["schedule"]["expr"] == "5 9 * * *"
    assert by_id["d41a1c0de160"]["schedule"]["expr"] == "0 16 * * *"
    assert all(job["next_run_at"] for job in by_id.values())
    assert (home / "config.yaml").read_text().count("timezone: America/New_York") == 1


def test_absolute_wrapper_invocation_works_outside_repository(tmp_path):
    home = seed_home(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    result = run_wrapper(home, "deploy", cwd=outside)
    assert result.returncode == 0, result.stderr
    assert "backup:" in result.stdout


def test_deploy_lock_contention_is_busy_and_does_not_mutate(tmp_path):
    home = seed_home(tmp_path)
    if sys.platform == "win32":
        pytest.skip("requires POSIX flock")
    jobs_before = (home / "cron" / "jobs.json").read_bytes()
    config_before = (home / "config.yaml").read_bytes()
    lock_path = home / "cron" / ".tick.lock"
    locker = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,sys;"
                "lock=open(sys.argv[1],'a+');"
                "fcntl.flock(lock,fcntl.LOCK_EX);"
                "print('locked',flush=True);"
                "sys.stdin.read()"
            ),
            str(lock_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert locker.stdout.readline().strip() == "locked"
    try:
        result = run_wrapper(home, "deploy")
    finally:
        locker.stdin.close()
        locker.wait(timeout=5)
    assert result.returncode == 75
    assert "busy" in result.stderr.lower()
    assert (home / "cron" / "jobs.json").read_bytes() == jobs_before
    assert (home / "config.yaml").read_bytes() == config_before
    assert not (home / "backups").exists()


def test_deploy_rolls_back_both_files_if_second_write_fails(tmp_path):
    home = seed_home(tmp_path, timezone="UTC")
    jobs_before = (home / "cron" / "jobs.json").read_bytes()
    config_before = (home / "config.yaml").read_bytes()
    result = run_wrapper(
        home,
        "deploy",
        extra_env={"IK_ERNIE_TEST_FAIL_AFTER_JOBS_WRITE": "1"},
    )
    assert result.returncode != 0
    assert "rollback complete" in result.stderr.lower()
    assert (home / "cron" / "jobs.json").read_bytes() == jobs_before
    assert (home / "config.yaml").read_bytes() == config_before
    assert len(list((home / "backups").glob("daily-goal-*"))) == 1


def test_deploy_uses_new_york_for_first_next_runs_even_from_utc(tmp_path):
    home = seed_home(tmp_path, timezone="UTC")
    result = run_wrapper(home, "deploy")
    assert result.returncode == 0, result.stderr
    jobs = {job["id"]: job for job in json.loads((home / "cron" / "jobs.json").read_text())["jobs"]}
    checkin = datetime.fromisoformat(jobs["bcfc1f4e449e"]["next_run_at"]).astimezone(ZoneInfo("America/New_York"))
    watchdog = datetime.fromisoformat(jobs["d41a1c0de160"]["next_run_at"]).astimezone(ZoneInfo("America/New_York"))
    assert (checkin.hour, checkin.minute) == (9, 5)
    assert (watchdog.hour, watchdog.minute) == (16, 0)
    assert checkin.utcoffset() == ZoneInfo("America/New_York").utcoffset(checkin)


def test_deploy_creates_collision_proof_restorable_backups(tmp_path):
    home = seed_home(tmp_path, timezone="UTC")
    original_jobs = (home / "cron" / "jobs.json").read_text()
    original_config = (home / "config.yaml").read_text()
    first = run_wrapper(home, "deploy")
    deployed_jobs = (home / "cron" / "jobs.json").read_text()
    deployed_config = (home / "config.yaml").read_text()
    second = run_wrapper(home, "deploy")
    assert first.returncode == second.returncode == 0
    first_backup = Path(re.search(r"backup: (.+)", first.stdout).group(1))
    second_backup = Path(re.search(r"backup: (.+)", second.stdout).group(1))
    assert len(list((home / "backups").glob("daily-goal-*"))) == 2
    assert first_backup != second_backup
    assert (first_backup / "jobs.json").read_text() == original_jobs
    assert (first_backup / "config.yaml").read_text() == original_config
    assert (second_backup / "jobs.json").read_text() == deployed_jobs
    assert (second_backup / "config.yaml").read_text() == deployed_config
    (home / "cron" / "jobs.json").write_text((first_backup / "jobs.json").read_text())
    (home / "config.yaml").write_text((first_backup / "config.yaml").read_text())
    assert (home / "cron" / "jobs.json").read_text() == original_jobs
    assert (home / "config.yaml").read_text() == original_config


def test_deploy_prefers_canonical_delivery_and_preserves_runtime_fields_and_modes(tmp_path):
    home = seed_home(tmp_path, deliver="telegram:-canonical")
    jobs_path = home / "cron" / "jobs.json"
    config_path = home / "config.yaml"
    data = json.loads(jobs_path.read_text())
    data["jobs"].insert(0, {"id": "legacy", "name": "ernie-telegram-daily-checkin", "deliver": "telegram:-legacy"})
    data["jobs"][1].update({"created_at": "old", "last_run_at": "then", "last_error": "bad", "repeat": {"times": None, "completed": 4}})
    jobs_path.write_text(json.dumps(data) + "\n")
    os.chmod(jobs_path, 0o640)
    os.chmod(config_path, 0o600)
    result = run_wrapper(home, "deploy")
    assert result.returncode == 0, result.stderr
    checkin = next(job for job in json.loads(jobs_path.read_text())["jobs"] if job["id"] == "bcfc1f4e449e")
    assert checkin["deliver"] == "telegram:-canonical"
    assert checkin["created_at"] == "old"
    assert checkin["last_run_at"] == "then"
    assert checkin["last_error"] == "bad"
    assert checkin["repeat"] == {"times": None, "completed": 4}
    assert jobs_path.stat().st_mode & 0o777 == 0o640
    assert config_path.stat().st_mode & 0o777 == 0o600


def test_deploy_rejects_non_telegram_delivery_before_backup(tmp_path):
    for index, delivery in enumerate(("local", "discord:123", "telegram:")):
        home = seed_home(tmp_path / str(index), deliver=delivery)
        before = (home / "cron" / "jobs.json").read_text()
        result = run_wrapper(home, "deploy")
        assert result.returncode != 0
        assert "Telegram" in result.stderr
        assert (home / "cron" / "jobs.json").read_text() == before
        assert not (home / "backups").exists()


def test_status_resolves_guarded_unset_home_to_test_default_without_writing(tmp_path):
    test_root = tmp_path / "stack"
    home = test_root / "config" / "ik-agents" / "hermes-ernie"
    home.mkdir(parents=True)
    config = home / "config.yaml"
    config.write_text("timezone: UTC\n")
    result = run_wrapper_with_test_root(test_root, "status")
    assert result.returncode == 0, result.stderr
    assert config.read_text() == "timezone: UTC\n"
    assert not (home / "cron").exists()


def test_path_guards_reject_empty_relative_root_and_symlink_escape(tmp_path):
    test_root = tmp_path / "stack"
    test_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    escaped = test_root / "escaped"
    escaped.symlink_to(outside, target_is_directory=True)
    for value, expected in (("", "empty"), ("relative", "absolute"), ("/", "must not be /"), (escaped, "under IK_ERNIE_TEST_ROOT")):
        result = run_wrapper_with_test_root(test_root, "status", home=value)
        assert result.returncode != 0
        assert expected in result.stderr


def test_deploy_refuses_missing_croniter_before_backup_or_write(tmp_path):
    home = seed_home(tmp_path)
    before = (home / "cron" / "jobs.json").read_text()
    result = run_wrapper_with_test_root(home.parent, "deploy", home=home, python=Path("/usr/bin/false"))
    assert result.returncode != 0
    assert "croniter" in result.stderr
    assert (home / "cron" / "jobs.json").read_text() == before
    assert not (home / "backups").exists()


def test_dry_run_delegates_to_the_task_five_nonmutating_path():
    source = WRAPPER.read_text()
    assert 'run_daily_goal_coordinator(mode="checkin", dry_run=True)' in source


def test_manual_trigger_uses_atomic_scheduler_outcomes():
    source = WRAPPER.read_text()
    assert "trigger_and_run_selected_job" in source
    assert 'outcome == "busy"' in source
    assert 'outcome != "executed"' in source


def test_deploy_refuses_to_invent_delivery_target_before_job_write(tmp_path):
    home = seed_home(tmp_path, deliver=None)
    before = (home / "cron" / "jobs.json").read_text()
    result = run_wrapper(home, "deploy")
    assert result.returncode != 0
    assert "refusing to invent one" in result.stderr
    assert (home / "cron" / "jobs.json").read_text() == before
    assert not (home / "backups").exists()


def test_deploy_removes_legacy_duplicates_by_id_or_name(tmp_path):
    home = seed_home(tmp_path)
    jobs_path = home / "cron" / "jobs.json"
    data = json.loads(jobs_path.read_text())
    data["jobs"].extend(
        [
            {"id": "legacy-checkin", "name": "ernie-telegram-daily-checkin", "deliver": "telegram:-123"},
            {"id": "d41a1c0de160", "name": "legacy-watchdog", "deliver": "telegram:-123"},
        ]
    )
    jobs_path.write_text(json.dumps(data) + "\n")

    result = run_wrapper(home, "deploy")

    assert result.returncode == 0
    jobs = json.loads(jobs_path.read_text())["jobs"]
    assert [job["id"] for job in jobs].count("bcfc1f4e449e") == 1
    assert [job["name"] for job in jobs].count("ernie-telegram-daily-checkin") == 1
    assert [job["id"] for job in jobs].count("d41a1c0de160") == 1
    assert [job["name"] for job in jobs].count("ernie-daily-goal-watchdog") == 1
