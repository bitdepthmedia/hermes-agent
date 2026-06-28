#!/usr/bin/env python3
"""Install/update Bert's Last30Days skill one tagged version behind upstream."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


REPO_URL = "https://github.com/mvanhorn/last30days-skill.git"
SKILL_SUBDIR = Path("skills") / "last30days"
STATE_PATH = Path("last30days") / "state.json"
BLOCKED_PATTERNS = (
    "axios@1.14.1",
    "axios@0.30.4",
    "plain-crypto-js",
    '"plain-crypto-js"',
)


@dataclass(frozen=True)
class TagRef:
    tag: str
    object_sha: str
    peeled_sha: str | None = None

    @property
    def commit_sha(self) -> str:
        return self.peeled_sha or self.object_sha


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()


def run(cmd: list[str], *, cwd: Path | None = None) -> str:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout


def parse_semver(tag: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", tag)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def list_tags(repo_url: str = REPO_URL) -> list[TagRef]:
    raw = run(["git", "ls-remote", "--tags", repo_url])
    by_tag: dict[str, TagRef] = {}
    peeled: dict[str, str] = {}

    for line in raw.splitlines():
        if not line.strip():
            continue
        sha, ref = line.split("\t", 1)
        if not ref.startswith("refs/tags/"):
            continue
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            peeled[name.removesuffix("^{}")] = sha
            continue
        if parse_semver(name):
            by_tag[name] = TagRef(tag=name, object_sha=sha)

    refs = [
        TagRef(tag=tag, object_sha=ref.object_sha, peeled_sha=peeled.get(tag))
        for tag, ref in by_tag.items()
    ]
    refs.sort(key=lambda ref: parse_semver(ref.tag) or (0, 0, 0), reverse=True)
    return refs


def target_ref(pin_tag: str | None = None) -> TagRef:
    refs = list_tags()
    if pin_tag:
        for ref in refs:
            if ref.tag == pin_tag:
                return ref
        raise RuntimeError(f"Requested tag {pin_tag!r} was not found upstream")
    if len(refs) < 2:
        raise RuntimeError("Need at least two semver tags to stay one version behind")
    return refs[1]


def scan_tree(path: Path) -> None:
    findings: list[str] = []
    for fpath in path.rglob("*"):
        if not fpath.is_file():
            continue
        try:
            text = fpath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in BLOCKED_PATTERNS:
            if pattern in text:
                findings.append(f"{fpath.relative_to(path)} contains {pattern}")

        if fpath.name == "package.json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                findings.append(f"{fpath.relative_to(path)} has invalid package.json")
                continue
            scripts = payload.get("scripts")
            if isinstance(scripts, dict):
                for hook in ("preinstall", "install", "postinstall", "prepare"):
                    if hook in scripts:
                        findings.append(
                            f"{fpath.relative_to(path)} defines scripts.{hook}"
                        )

    if findings:
        joined = "\n".join(f"- {finding}" for finding in findings)
        raise RuntimeError(f"Last30Days safety scan blocked install:\n{joined}")


def installed_state(home: Path) -> dict:
    state_file = home / STATE_PATH
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def install(ref: TagRef, home: Path, *, force: bool = False) -> dict:
    skills_dir = home / "skills"
    dest = skills_dir / "last30days"
    state_file = home / STATE_PATH
    current = installed_state(home)
    if not force and current.get("tag") == ref.tag and dest.exists():
        return {"changed": False, "tag": ref.tag, "commit": ref.commit_sha, "path": str(dest)}

    with tempfile.TemporaryDirectory(prefix="bert-last30days-") as tmp:
        repo_dir = Path(tmp) / "repo"
        run(["git", "clone", "--depth", "1", "--branch", ref.tag, REPO_URL, str(repo_dir)])
        src = repo_dir / SKILL_SUBDIR
        if not (src / "SKILL.md").exists():
            raise RuntimeError(f"Upstream tag {ref.tag} has no {SKILL_SUBDIR}/SKILL.md")
        scan_tree(src)

        skills_dir.mkdir(parents=True, exist_ok=True)
        tmp_dest = skills_dir / f".last30days.tmp.{os.getpid()}"
        if tmp_dest.exists():
            shutil.rmtree(tmp_dest)
        shutil.copytree(src, tmp_dest)
        if dest.exists():
            backup = skills_dir / f"last30days.bak.{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            shutil.move(str(dest), str(backup))
        os.replace(tmp_dest, dest)

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "tag": ref.tag,
        "commit": ref.commit_sha,
        "repo": REPO_URL,
        "policy": "one-version-behind-latest-semver-tag",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "path": str(dest),
    }
    state_file.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.chmod(state_file, 0o600)
    return {"changed": True, **state}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", default=str(hermes_home()))
    parser.add_argument("--pin-tag", default=os.getenv("BERT_LAST30DAYS_PIN_TAG"))
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args(argv)

    home = Path(args.home).expanduser()
    if args.status:
        print(json.dumps(installed_state(home), indent=2))
        return 0

    ref = target_ref(args.pin_tag)
    result = install(ref, home, force=args.force)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
