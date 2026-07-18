import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "ik_profiles" / "hermes-ernie" / "cron"
WRAPPER = ROOT / "scripts" / "ik-ernie-daily-goal"


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


def seed_home(tmp_path, deliver="telegram:-123"):
    home = tmp_path / "hermes-ernie"
    (home / "cron").mkdir(parents=True)
    (home / "config.yaml").write_text("timezone: ''\n")
    job = {
        "id": "bcfc1f4e449e",
        "name": "ernie-telegram-daily-checkin",
        "deliver": deliver,
        "schedule": {"kind": "interval", "minutes": 1440},
        "last_status": "error",
    }
    (home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [job]}) + "\n")
    return home


def run_wrapper(home, action):
    env = dict(os.environ)
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
