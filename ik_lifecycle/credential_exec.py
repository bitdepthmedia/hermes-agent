"""Execute a frozen command with assignment-only opaque credential files."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import sys
from typing import Mapping, Sequence


_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_ROUTER_KEYS = frozenset({"ERNIE_ROUTER_API_KEY"})
_EXECUTION_KEYS = frozenset(
    {
        "BASH_ENV",
        "CDPATH",
        "ENV",
        "GLOBIGNORE",
        "HOME",
        "IFS",
        "PATH",
        "SHELL",
        "SHELLOPTS",
        "VIRTUAL_ENV",
    }
)
_EXECUTION_PREFIXES = ("DYLD_", "LD_", "NODE_", "NPM_", "PIP_", "PYTHON", "UV_")
_TRANSPORT_KEYS = frozenset(
    {
        "ALL_PROXY",
        "AWS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "GIT_SSL_CAINFO",
        "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "all_proxy",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)
_SEALED_RUNTIME_KEYS = frozenset(
    {
        "HERMES_HOME",
        "HERMES_WEB_DIST",
        "IK_CELL_ROOT",
        "IK_CELL_ID",
        "IK_CELL_SERVICE_ROLE",
        "IK_CELL_CREDENTIAL_FILE",
        "IK_CELL_SHARED_CREDENTIAL_FILE",
        "IK_RELEASE_IMAGE",
        "IK_RELEASE_MOUNT",
        "IK_ROUTER_CONFIG",
        "IK_MODEL_BASE_URL",
        "IK_SERVICE_HOST",
        "IK_SERVICE_PORT",
        "OLLAMA_BASE_URL",
        "OLLAMA_HOST",
        "OLLAMA_MODELS",
        "OLLAMA_MODEL",
        "ERNIE_ROUTER_MODEL_NAME",
        "ERNIE_FAST_BASE_URL",
        "ERNIE_OPERATOR_BASE_URL",
    }
)
_SEALED_RUNTIME_PREFIXES = ("API_SERVER_",)


class CredentialExecError(RuntimeError):
    """A deliberately value- and path-redacted credential boundary failure."""


def _value(raw: str) -> str:
    value = raw.strip()
    if not value:
        return ""
    if value[0] in "'\"":
        try:
            tokens = shlex.split(value, comments=True, posix=True)
        except ValueError as exc:
            raise CredentialExecError("credential_assignment_invalid") from exc
        if len(tokens) != 1:
            raise CredentialExecError("credential_assignment_invalid")
        return tokens[0]
    value = re.sub(r"\s+#.*$", "", value).rstrip()
    if "\x00" in value:
        raise CredentialExecError("credential_assignment_invalid")
    return value


def credential_key_allowed(key: str, *, policy: str, occupied: frozenset[str] = frozenset()) -> bool:
    if policy == "router":
        return key in _ROUTER_KEYS and key not in occupied
    if policy == "compatibility":
        return not (
            key in occupied
            or key in _EXECUTION_KEYS
            or key in _TRANSPORT_KEYS
            or key in _SEALED_RUNTIME_KEYS
            or key.startswith(_EXECUTION_PREFIXES)
            or key.startswith(_SEALED_RUNTIME_PREFIXES)
        )
    raise CredentialExecError("credential_policy_invalid")


def read_credential_assignments(path: Path) -> dict[str, str]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise CredentialExecError("credential_file_invalid")
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise CredentialExecError("credential_file_invalid") from exc
    assignments: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if separator != "=" or not _KEY.fullmatch(key):
            raise CredentialExecError("credential_assignment_invalid")
        assignments[key] = _value(raw_value)
    return assignments


def render_credential_assignments(assignments: Mapping[str, str]) -> str:
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in sorted(assignments.items()))


def load_credential_environment(
    paths: Sequence[Path],
    *,
    base: Mapping[str, str] | None = None,
    policy: str | None = None,
) -> dict[str, str]:
    """Parse dotenv-style assignments without shell expansion or value output."""

    if policy not in {None, "router", "compatibility"}:
        raise CredentialExecError("credential_policy_invalid")
    environment = dict(os.environ if base is None else base)
    occupied = frozenset(environment)
    for raw_path in paths:
        for key, value in read_credential_assignments(Path(raw_path)).items():
            if policy is not None and not credential_key_allowed(key, policy=policy, occupied=occupied):
                raise CredentialExecError("credential_key_not_allowed")
            environment[key] = value
    return environment


def _arguments(argv: Sequence[str]) -> tuple[str | None, tuple[Path, ...], tuple[str, ...]]:
    credentials: list[Path] = []
    index = 0
    policy: str | None = None
    if len(argv) >= 2 and argv[0] == "--policy":
        policy = argv[1]
        index = 2
    while index < len(argv) and argv[index] == "--credential":
        if index + 1 >= len(argv):
            raise CredentialExecError("credential_exec_arguments_invalid")
        credentials.append(Path(argv[index + 1]))
        index += 2
    if index >= len(argv) or argv[index] != "--" or index + 1 >= len(argv):
        raise CredentialExecError("credential_exec_arguments_invalid")
    if not credentials:
        raise CredentialExecError("credential_exec_arguments_invalid")
    return policy, tuple(credentials), tuple(argv[index + 1 :])


def main(argv: Sequence[str] | None = None) -> int:
    try:
        policy, credentials, command = _arguments(tuple(sys.argv[1:] if argv is None else argv))
        environment = load_credential_environment(credentials, policy=policy)
        os.execvpe(command[0], command, environment)
    except (CredentialExecError, OSError):
        sys.stderr.write("credential_exec_blocked\n")
        return 88
    return 88


if __name__ == "__main__":
    raise SystemExit(main())
