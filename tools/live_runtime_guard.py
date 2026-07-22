"""Guards for live gateway deployments that run from a production checkout."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path


_READ_ONLY_BASE_COMMANDS = {
    "cat",
    "find",
    "git",
    "grep",
    "head",
    "ls",
    "pwd",
    "rg",
    "sed",
    "stat",
    "tail",
    "wc",
}

_READ_ONLY_GIT_SUBCOMMANDS = {
    "branch",
    "diff",
    "grep",
    "log",
    "rev-list",
    "rev-parse",
    "show",
    "status",
}

_SHELL_CHAIN_RE = re.compile(r"\s*(?:&&|\|\||;|\n)\s*")
_CD_RE = re.compile(r"(?:^|[;&|\n]\s*)cd\s+([^\s;&|]+)")


def protected_checkout_root() -> str | None:
    """Return the configured protected checkout path, if live guard is enabled."""
    root = os.getenv("HERMES_PROTECTED_CHECKOUT", "").strip()
    if not root:
        return None
    try:
        return os.path.realpath(os.path.expanduser(root))
    except Exception:
        return None


def path_is_protected(path: str | os.PathLike[str]) -> bool:
    root = protected_checkout_root()
    if not root:
        return False
    try:
        resolved = os.path.realpath(os.path.expanduser(str(path)))
    except Exception:
        return False
    return resolved == root or resolved.startswith(root + os.sep)


def protected_write_error(path: str | os.PathLike[str]) -> str | None:
    """Return a user-facing denial if a write targets the protected checkout."""
    if not path_is_protected(path):
        return None
    root = protected_checkout_root() or str(path)
    return (
        f"Write denied: '{path}' is inside protected live checkout '{root}'. "
        "Use the deployment workflow instead of editing the running gateway tree."
    )


def _split_command(command: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for segment in _SHELL_CHAIN_RE.split(command.strip()):
        if not segment:
            continue
        try:
            commands.append(shlex.split(segment))
        except ValueError:
            return []
    return commands


def _command_references_protected_path(command: str) -> bool:
    root = protected_checkout_root()
    if not root:
        return False
    expanded = command.replace("$HERMES_PROTECTED_CHECKOUT", root)
    expanded = expanded.replace("${HERMES_PROTECTED_CHECKOUT}", root)
    return root in expanded


def _cds_into_protected_path(command: str, cwd: str | None) -> bool:
    for match in _CD_RE.finditer(command):
        raw_path = match.group(1).strip("'\"")
        if raw_path in {"$HERMES_PROTECTED_CHECKOUT", "${HERMES_PROTECTED_CHECKOUT}"}:
            return True
        candidate = raw_path
        if not os.path.isabs(candidate) and cwd:
            candidate = str(Path(cwd) / candidate)
        if path_is_protected(candidate):
            return True
    return False


def _is_read_only_command(command: str) -> bool:
    parts = _split_command(command)
    if not parts:
        return False
    for argv in parts:
        base = os.path.basename(argv[0])
        if base not in _READ_ONLY_BASE_COMMANDS:
            return False
        if base == "git":
            subcommand = next((arg for arg in argv[1:] if not arg.startswith("-")), "")
            if subcommand not in _READ_ONLY_GIT_SUBCOMMANDS:
                return False
        if base == "sed" and any(arg.startswith("-i") or arg == "--in-place" for arg in argv[1:]):
            return False
        if any(token in {">", ">>"} for token in argv):
            return False
    return True


def protected_terminal_error(command: str, cwd: str | None = None) -> str | None:
    """Deny non-read-only terminal commands aimed at the protected checkout."""
    root = protected_checkout_root()
    if not root:
        return None
    cwd_protected = bool(cwd and path_is_protected(cwd))
    command_targets_root = _command_references_protected_path(command) or _cds_into_protected_path(command, cwd)
    if not (cwd_protected or command_targets_root):
        return None
    if _is_read_only_command(command):
        return None
    return (
        f"Command denied: protected live checkout '{root}' is read-only from gateway sessions. "
        "Use the deployment workflow for code changes, git mutation, tests, or pushes."
    )
