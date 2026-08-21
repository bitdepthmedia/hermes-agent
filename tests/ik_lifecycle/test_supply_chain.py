from __future__ import annotations

import json
from pathlib import Path

import pytest

from ik_lifecycle.cli import main
from ik_lifecycle.models import LifecycleBlockedError
from ik_lifecycle.supply_chain import inspect_manifests


@pytest.mark.parametrize(
    ("relative_path", "content", "surface"),
    [
        ("package.json", '{"dependencies":{"axios":"1.14.1"}}', "manifest"),
        (
            "package-lock.json",
            '{"packages":{"node_modules/axios":{"version":"1.14.1"}}}',
            "lockfile",
        ),
        ("node_modules/axios/package.json", '{"name":"axios","version":"1.14.1"}', "installed"),
        (".npm/_logs/debug.log", "resolved axios@1.14.1", "cache_or_log"),
        ("Dockerfile", "RUN npm install axios@1.14.1", "install_command"),
    ],
)
def test_forbidden_implementation_surfaces_block(
    tmp_path: Path,
    relative_path: str,
    content: str,
    surface: str,
) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    report = inspect_manifests(tmp_path)

    assert report.status == "BLOCKED"
    assert report.code == "forbidden_dependency"
    assert report.findings[0].package == "axios"
    assert report.findings[0].version == "1.14.1"
    assert report.findings[0].surface == surface


def test_missing_candidate_root_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(LifecycleBlockedError) as error:
        inspect_manifests(tmp_path / "missing")

    assert error.value.code == "candidate_source_missing"


@pytest.mark.parametrize(
    ("relative_path", "content", "surface"),
    [
        ("pyproject.toml", 'dependencies = ["plain-crypto-js==4.2.1"]', "manifest"),
        ("requirements-build.txt", "axios==0.30.4\n", "manifest"),
        ("uv.lock", '[[package]]\nname = "axios"\nversion = "1.14.1"\n', "lockfile"),
        (".github/workflows/build.yml", "run: npm install axios@1.14.1", "install_command"),
    ],
)
def test_python_lock_and_ci_install_surfaces_block(
    tmp_path: Path,
    relative_path: str,
    content: str,
    surface: str,
) -> None:
    path = tmp_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    report = inspect_manifests(tmp_path)

    assert report.status == "BLOCKED"
    assert report.findings[0].surface == surface


def test_passive_policy_safeguard_and_fixture_mentions_are_clear(tmp_path: Path) -> None:
    files = {
        "docs/policy.md": "Never install axios@1.14.1.",
        "scripts/supply_chain_safeguard.py": 'FORBIDDEN = "plain-crypto-js@4.2.1"',
        "tests/fixtures/install.sh": "npm install axios@0.30.4",
    }
    for relative_path, content in files.items():
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    report = inspect_manifests(tmp_path)

    assert report.status == "CLEAR"
    assert report.findings == ()


def test_executable_test_surface_is_not_blanket_exempted(tmp_path: Path) -> None:
    path = tmp_path / "tests" / "runtime-install.sh"
    path.parent.mkdir(parents=True)
    path.write_text("npm install axios@1.14.1\n", encoding="utf-8")

    report = inspect_manifests(tmp_path)

    assert report.status == "BLOCKED"
    assert report.findings[0].surface == "install_command"


def test_changed_lifecycle_hooks_are_inventoried_without_execution(tmp_path: Path) -> None:
    base = tmp_path / "base"
    source = tmp_path / "source"
    base.mkdir()
    source.mkdir()
    marker = tmp_path / "hook-ran"
    (base / "package.json").write_text(
        json.dumps({"scripts": {"postinstall": "echo old"}}),
        encoding="utf-8",
    )
    (source / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "postinstall": f"python -c \"from pathlib import Path; Path({str(marker)!r}).write_text('ran')\"",
                    "prepare": "python build.py",
                }
            }
        ),
        encoding="utf-8",
    )

    report = inspect_manifests(source, base)

    assert report.status == "CLEAR"
    assert [(change.hook, change.change) for change in report.hook_changes] == [
        ("postinstall", "changed"),
        ("prepare", "added"),
    ]
    assert not marker.exists()


def test_static_plan_uses_frozen_audit_commands_only(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text('{"packages":{}}', encoding="utf-8")
    nested = tmp_path / "web"
    nested.mkdir()
    (nested / "package-lock.json").write_text('{"packages":{}}', encoding="utf-8")

    report = inspect_manifests(tmp_path)

    assert report.status == "CLEAR"
    assert [(command.workdir, command.argv) for command in report.planned_commands] == [
        (".", ("uv", "sync", "--frozen", "--no-install-project")),
        (".", ("npm", "ci", "--ignore-scripts")),
        ("web", ("npm", "ci", "--ignore-scripts")),
    ]


def test_cli_supply_chain_emits_static_receipt_without_running_hooks(tmp_path: Path, capsys) -> None:
    marker = tmp_path / "hook-ran"
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"postinstall": f"touch {marker}"}}),
        encoding="utf-8",
    )

    exit_code = main(["supply-chain", "--candidate", str(tmp_path)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["receipt"]["kind"] == "supply_chain_static"
    assert output["receipt"]["status"] == "CLEAR"
    assert output["receipt"]["data"]["dependency_execution_performed"] is False
    assert not marker.exists()
