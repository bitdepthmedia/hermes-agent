"""Static, non-executing supply-chain inspection for staged candidates."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping

from .models import LifecycleBlockedError


_FORBIDDEN = {
    ("axios", "1.14.1"),
    ("axios", "0.30.4"),
    ("plain-crypto-js", "4.2.1"),
}
_HOOKS = ("preinstall", "install", "postinstall", "prepare")
_PASSIVE_PARTS = {
    "docs",
    "doc",
    "fixtures",
    "fixture",
    "archives",
    "archive",
    ".pytest_cache",
    "__pycache__",
}
_LOCK_NAMES = {"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock"}
_INSTALL_FILES = {"Dockerfile", "Containerfile", "Makefile"}
_INSTALL_COMMAND = re.compile(r"(?:npm|pnpm|yarn|bun)\s+(?:install|add|i)\b", re.IGNORECASE)


@dataclass(frozen=True)
class SupplyChainFinding:
    package: str
    version: str
    surface: str
    path: str


@dataclass(frozen=True)
class HookChange:
    path: str
    hook: str
    change: str


@dataclass(frozen=True)
class PlannedCommand:
    workdir: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class SupplyChainReport:
    status: str
    code: str
    findings: tuple[SupplyChainFinding, ...]
    hook_changes: tuple[HookChange, ...]
    planned_commands: tuple[PlannedCommand, ...]
    artifact_sha256: Mapping[str, str]


def _relative(path: Path, root: Path) -> str:
    value = path.relative_to(root).as_posix()
    return value or "."


def _is_passive(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    parts = {part.lower() for part in relative.parts[:-1]}
    if parts & _PASSIVE_PARTS:
        return True
    name = path.name.lower()
    return (
        "supply_chain" in name
        or "supply-chain" in name
        or "safeguard" in name
        or name in {"test_supply_chain.py", "test_supply_chain_safeguard.py"}
    )


def _iter_files(root: Path) -> Iterator[Path]:
    if not root.exists():
        return
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def _package_json_findings(path: Path, root: Path, surface: str) -> list[SupplyChainFinding]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    findings: list[SupplyChainFinding] = []
    if surface == "installed":
        pair = (document.get("name"), document.get("version"))
        if pair in _FORBIDDEN:
            findings.append(SupplyChainFinding(pair[0], pair[1], surface, _relative(path, root)))
        return findings
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        dependencies = document.get(field, {})
        if not isinstance(dependencies, dict):
            continue
        for package, constraint in dependencies.items():
            for forbidden_package, version in _FORBIDDEN:
                if package == forbidden_package and isinstance(constraint, str) and re.search(
                    rf"(?<![0-9]){re.escape(version)}(?![0-9])", constraint
                ):
                    findings.append(SupplyChainFinding(package, version, surface, _relative(path, root)))
    return findings


def _text_findings(path: Path, root: Path, surface: str, *, require_install_command: bool = False) -> list[SupplyChainFinding]:
    text = _read_text(path)
    if require_install_command and not _INSTALL_COMMAND.search(text):
        return []
    findings: list[SupplyChainFinding] = []
    for package, version in sorted(_FORBIDDEN):
        pattern = rf"{re.escape(package)}(?:@|[^A-Za-z0-9_.-]+).{{0,80}}?{re.escape(version)}"
        if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
            findings.append(SupplyChainFinding(package, version, surface, _relative(path, root)))
    return findings


def _hooks(root: Path) -> dict[tuple[str, str], str]:
    result: dict[tuple[str, str], str] = {}
    for path in _iter_files(root):
        if path.name != "package.json" or "node_modules" in path.parts or _is_passive(path, root):
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        scripts = document.get("scripts", {})
        if not isinstance(scripts, dict):
            continue
        for hook in _HOOKS:
            value = scripts.get(hook)
            if isinstance(value, str):
                result[(_relative(path, root), hook)] = value
    return result


def _hook_changes(source: Path, base: Path | None) -> tuple[HookChange, ...]:
    current = _hooks(source)
    previous = _hooks(base) if base else {}
    changes: list[HookChange] = []
    for key in sorted(set(current) | set(previous)):
        before = previous.get(key)
        after = current.get(key)
        if before == after:
            continue
        change = "added" if before is None else "removed" if after is None else "changed"
        changes.append(HookChange(key[0], key[1], change))
    return tuple(changes)


def _planned_commands(root: Path) -> tuple[PlannedCommand, ...]:
    plans: list[PlannedCommand] = []
    if (root / "uv.lock").is_file():
        plans.append(PlannedCommand(".", ("uv", "sync", "--frozen", "--no-install-project")))
    lock_dirs = sorted(
        {
            path.parent
            for path in _iter_files(root)
            if path.name in _LOCK_NAMES and "node_modules" not in path.parts and not _is_passive(path, root)
        },
        key=lambda value: (len(value.relative_to(root).parts), value.relative_to(root).as_posix()),
    )
    for directory in lock_dirs:
        workdir = _relative(directory, root)
        if (directory / "package-lock.json").is_file() or (directory / "npm-shrinkwrap.json").is_file():
            argv = ("npm", "ci", "--ignore-scripts")
        elif (directory / "pnpm-lock.yaml").is_file():
            argv = ("pnpm", "install", "--frozen-lockfile", "--ignore-scripts")
        elif (directory / "yarn.lock").is_file():
            argv = ("yarn", "install", "--immutable", "--ignore-scripts")
        else:
            argv = ("bun", "install", "--frozen-lockfile", "--ignore-scripts")
        plans.append(PlannedCommand(workdir, argv))
    return tuple(plans)


def _artifact_digests(root: Path) -> dict[str, str]:
    digests: dict[str, str] = {}
    for path in _iter_files(root):
        if (
            path.name == "package.json"
            or path.name in _LOCK_NAMES
            or path.name == "uv.lock"
            or path.name == "pyproject.toml"
            or path.name.startswith("requirements")
        ):
            if "node_modules" in path.parts or _is_passive(path, root):
                continue
            digests[_relative(path, root)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digests


def inspect_manifests(source: Path, base: Path | None = None) -> SupplyChainReport:
    """Inspect supply-chain surfaces without resolving or executing dependencies."""

    root = Path(source).resolve()
    base_root = Path(base).resolve() if base is not None else None
    if not root.is_dir():
        raise LifecycleBlockedError("candidate_source_missing", f"Candidate source is not a directory: {source}")
    if base_root is not None and not base_root.is_dir():
        raise LifecycleBlockedError("comparison_source_missing", f"Comparison source is not a directory: {base}")
    findings: list[SupplyChainFinding] = []
    for path in _iter_files(root):
        if _is_passive(path, root):
            continue
        relative_parts = path.relative_to(root).parts
        if path.name == "package.json":
            surface = "installed" if "node_modules" in relative_parts else "manifest"
            findings.extend(_package_json_findings(path, root, surface))
        elif path.name in _LOCK_NAMES:
            findings.extend(_text_findings(path, root, "lockfile"))
        elif path.name == "uv.lock":
            findings.extend(_text_findings(path, root, "lockfile"))
        elif path.name == "pyproject.toml" or path.name.startswith("requirements"):
            findings.extend(_text_findings(path, root, "manifest"))
        elif any(part in {".npm", ".pnpm-store", ".yarn", "logs", "cache"} for part in relative_parts[:-1]):
            findings.extend(_text_findings(path, root, "cache_or_log"))
        elif path.name in _INSTALL_FILES or path.suffix.lower() in {".sh", ".bash", ".zsh", ".ps1"}:
            findings.extend(_text_findings(path, root, "install_command", require_install_command=True))
        elif ".github" in relative_parts and "workflows" in relative_parts and path.suffix.lower() in {".yml", ".yaml"}:
            findings.extend(_text_findings(path, root, "install_command", require_install_command=True))
    ordered = tuple(sorted(set(findings), key=lambda item: (item.path, item.package, item.version, item.surface)))
    return SupplyChainReport(
        status="BLOCKED" if ordered else "CLEAR",
        code="forbidden_dependency" if ordered else "clear",
        findings=ordered,
        hook_changes=_hook_changes(root, base_root),
        planned_commands=_planned_commands(root),
        artifact_sha256=_artifact_digests(root),
    )
