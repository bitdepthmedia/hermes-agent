"""Resolve a live profile from launchd metadata without opening profile content."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import plistlib
import re
import shlex

from .models import LifecycleBlockedError


@dataclass(frozen=True)
class ResolvedLaunchdProfile:
    label: str
    wrapper_path: Path
    profile_root: Path
    declared_wrapper_path_sha256: str
    declared_profile_path_sha256: str
    profile_path_sha256: str
    service_definition_sha256: str
    wrapper_sha256: str


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def resolve_launchd_profile(plist_path: Path, *, expected_label: str) -> ResolvedLaunchdProfile:
    plist = Path(plist_path).absolute()
    if plist.is_symlink() or not plist.is_file():
        raise LifecycleBlockedError("service_definition_invalid", "launchd service definition is invalid")
    try:
        document = plistlib.loads(plist.read_bytes())
    except Exception as exc:
        raise LifecycleBlockedError("service_definition_invalid", "launchd service definition is invalid") from exc
    arguments = document.get("ProgramArguments")
    if document.get("Label") != expected_label or not isinstance(arguments, list) or not arguments:
        raise LifecycleBlockedError("service_identity_mismatch", "launchd service identity is invalid")
    wrapper = Path(str(arguments[0])).absolute()
    if wrapper.is_symlink() or not wrapper.is_file():
        raise LifecycleBlockedError("service_wrapper_invalid", "launchd service wrapper is invalid")
    source = wrapper.read_text(encoding="utf-8")
    assignments = re.findall(r"^\s*export\s+HERMES_HOME=(.+?)\s*$", source, flags=re.MULTILINE)
    if len(assignments) != 1:
        raise LifecycleBlockedError("profile_assignment_ambiguous", "live profile assignment is ambiguous")
    raw = assignments[0]
    if any(marker in raw for marker in ("$", "`", "$(", "${")):
        raise LifecycleBlockedError("profile_assignment_dynamic", "live profile assignment is dynamic")
    try:
        values = shlex.split(raw, posix=True)
    except ValueError as exc:
        raise LifecycleBlockedError("profile_assignment_invalid", "live profile assignment is invalid") from exc
    if len(values) != 1:
        raise LifecycleBlockedError("profile_assignment_invalid", "live profile assignment is invalid")
    profile = Path(values[0])
    if not profile.is_absolute() or not profile.exists() or not profile.is_dir():
        raise LifecycleBlockedError("profile_root_missing", "live profile root is missing")
    if profile.is_symlink():
        raise LifecycleBlockedError("profile_root_symlink", "live profile root contains a symlink")
    resolved = profile.resolve()
    return ResolvedLaunchdProfile(
        label=expected_label,
        wrapper_path=wrapper.resolve(),
        profile_root=resolved,
        declared_wrapper_path_sha256=hashlib.sha256(str(wrapper).encode()).hexdigest(),
        declared_profile_path_sha256=hashlib.sha256(str(profile).encode()).hexdigest(),
        profile_path_sha256=hashlib.sha256(str(resolved).encode()).hexdigest(),
        service_definition_sha256=hashlib.sha256(plist.read_bytes()).hexdigest(),
        wrapper_sha256=hashlib.sha256(wrapper.read_bytes()).hexdigest(),
    )
