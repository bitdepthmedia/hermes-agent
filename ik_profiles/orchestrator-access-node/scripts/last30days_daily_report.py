#!/usr/bin/env python3
"""Collect daily Last30Days evidence for the cron report."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import last30days_sync


DEFAULT_TOPIC = "AI coding agents Codex Claude Code Hermes Agent skills"


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()


def ensure_user_bin_on_path() -> None:
    user_bin = Path.home() / ".local" / "bin"
    path_parts = os.environ.get("PATH", "").split(os.pathsep)
    if str(user_bin) not in path_parts:
        os.environ["PATH"] = os.pathsep.join([str(user_bin), *[part for part in path_parts if part]])


def find_python() -> str:
    override = os.environ.get("LAST30DAYS_PYTHON")
    candidates = [override] if override else []
    candidates.extend(["python3.14", "python3.13", "python3.12", "python3"])
    for candidate in candidates:
        if not candidate:
            continue
        path = shutil.which(candidate) if "/" not in candidate else candidate
        if not path:
            continue
        proc = subprocess.run(
            [path, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            return path
    raise RuntimeError("Last30Days requires Python 3.12+; none was found on PATH")


def run_last30days(home: Path, topic: str, save_dir: Path) -> str:
    script = home / "skills" / "last30days" / "scripts" / "last30days.py"
    if not script.exists():
        raise RuntimeError(f"Last30Days engine missing at {script}")
    py = find_python()
    env = os.environ.copy()
    env["HERMES_HOME"] = str(home)
    env["LAST30DAYS_MEMORY_DIR"] = str(save_dir)
    cmd = [
        py,
        str(script),
        topic,
        "--no-browser-cookies",
        "--emit",
        "compact",
        "--save-dir",
        str(save_dir),
        "--save-suffix",
        datetime.now().strftime("orchestrator-daily-%Y%m%d"),
    ]
    proc = subprocess.run(
        cmd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=int(os.environ.get("ORCHESTRATOR_LAST30DAYS_TIMEOUT", "420")),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"Last30Days failed with exit {proc.returncode}:\n{proc.stdout}")
    return proc.stdout


def main() -> int:
    ensure_user_bin_on_path()
    home = hermes_home()
    save_dir = Path(os.environ.get("ORCHESTRATOR_LAST30DAYS_SAVE_DIR", home / "last30days" / "reports")).expanduser()
    save_dir.mkdir(parents=True, exist_ok=True)

    ref = last30days_sync.target_ref(os.environ.get("ORCHESTRATOR_LAST30DAYS_PIN_TAG"))
    sync_result = last30days_sync.install(ref, home)
    topic = os.environ.get("ORCHESTRATOR_LAST30DAYS_TOPIC", DEFAULT_TOPIC).strip() or DEFAULT_TOPIC
    ytdlp_status = "available" if shutil.which("yt-dlp") else "missing"
    raw = run_last30days(home, topic, save_dir)

    print(f"# Orchestrator Last30Days Daily Brief Input - {datetime.now().strftime('%Y-%m-%d')}")
    print()
    print("## Runtime Status")
    print(f"- Last30Days tag: {sync_result.get('tag')} ({sync_result.get('commit')})")
    print(f"- Update policy: one version behind latest semver tag")
    print(f"- Skill changed this run: {sync_result.get('changed')}")
    print(f"- yt-dlp: {ytdlp_status}")
    print(f"- Topic: {topic}")
    print(f"- Raw report directory: {save_dir}")
    print()
    print("## Raw Last30Days Output")
    print()
    print(raw)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"# Orchestrator Last30Days Daily Brief Failed\n\n{exc}", file=sys.stderr)
        raise
