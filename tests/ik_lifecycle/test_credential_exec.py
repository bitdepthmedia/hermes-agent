from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from ik_lifecycle.credential_exec import CredentialExecError, load_credential_environment


def test_assignment_parser_never_evaluates_shell_content(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    credential = tmp_path / "opaque.env"
    credential.write_text(
        "PLAIN=value\n"
        "QUOTED=\"value with spaces\"\n"
        f"SHELL=$(touch {marker})\n"
        "export TOKEN='literal-token'\n",
        encoding="utf-8",
    )

    environment = load_credential_environment((credential,), base={})

    assert environment == {
        "PLAIN": "value",
        "QUOTED": "value with spaces",
        "SHELL": f"$(touch {marker})",
        "TOKEN": "literal-token",
    }
    assert not marker.exists()


def test_parser_rejects_symlinks_invalid_keys_and_unclosed_quotes(tmp_path: Path) -> None:
    real = tmp_path / "real.env"
    real.write_text("TOKEN=value\n", encoding="utf-8")
    link = tmp_path / "link.env"
    link.symlink_to(real)
    invalid_key = tmp_path / "invalid-key.env"
    invalid_key.write_text("BAD-KEY=value\n", encoding="utf-8")
    invalid_quote = tmp_path / "invalid-quote.env"
    invalid_quote.write_text("TOKEN='value\n", encoding="utf-8")

    for path in (link, invalid_key, invalid_quote):
        with pytest.raises(CredentialExecError) as caught:
            load_credential_environment((path,), base={})
        assert str(path) not in str(caught.value)


def test_cli_executes_with_literal_credentials_without_logging_values(tmp_path: Path) -> None:
    credential = tmp_path / "opaque.env"
    credential.write_text("IK_SYNTHETIC_TOKEN='literal synthetic value'\n", encoding="utf-8")
    command = (
        sys.executable,
        "-m",
        "ik_lifecycle.credential_exec",
        "--credential",
        str(credential),
        "--",
        sys.executable,
        "-c",
        "import os; raise SystemExit(0 if os.environ.get('IK_SYNTHETIC_TOKEN') == 'literal synthetic value' else 9)",
    )

    result = subprocess.run(command, check=False, capture_output=True, text=True, env=os.environ.copy())

    assert result.returncode == 0
    assert "literal synthetic value" not in result.stdout + result.stderr


def test_cli_failure_is_redacted(tmp_path: Path) -> None:
    credential = tmp_path / "opaque.env"
    credential.write_text("SECRET_NAME='unterminated\n", encoding="utf-8")

    result = subprocess.run(
        (
            sys.executable,
            "-m",
            "ik_lifecycle.credential_exec",
            "--credential",
            str(credential),
            "--",
            "/usr/bin/true",
        ),
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 88
    assert result.stdout == ""
    assert result.stderr == "credential_exec_blocked\n"
    assert "SECRET_NAME" not in result.stderr


@pytest.mark.parametrize("key", ("PATH", "PYTHONPATH", "DYLD_INSERT_LIBRARIES", "OLLAMA_BASE_URL", "IK_ROUTER_CONFIG"))
def test_router_policy_rejects_runtime_control_keys(tmp_path: Path, key: str) -> None:
    credential = tmp_path / "opaque.env"
    credential.write_text(f"{key}=synthetic\n", encoding="utf-8")

    with pytest.raises(CredentialExecError):
        load_credential_environment((credential,), base={}, policy="router")


def test_router_policy_allows_only_its_opaque_authentication_key(tmp_path: Path) -> None:
    credential = tmp_path / "opaque.env"
    credential.write_text("ERNIE_ROUTER_API_KEY=synthetic\n", encoding="utf-8")

    result = load_credential_environment((credential,), base={}, policy="router")

    assert result == {"ERNIE_ROUTER_API_KEY": "synthetic"}


def test_compatibility_policy_cannot_override_service_environment_or_loader_controls(tmp_path: Path) -> None:
    credential = tmp_path / "opaque.env"
    credential.write_text("IK_SERVICE_PORT=9999\n", encoding="utf-8")
    with pytest.raises(CredentialExecError):
        load_credential_environment((credential,), base={"IK_SERVICE_PORT": "18426"}, policy="compatibility")

    credential.write_text("DYLD_INSERT_LIBRARIES=synthetic\n", encoding="utf-8")
    with pytest.raises(CredentialExecError):
        load_credential_environment((credential,), base={}, policy="compatibility")


@pytest.mark.parametrize(
    "key",
    ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"),
)
def test_compatibility_policy_rejects_transport_redirection(tmp_path: Path, key: str) -> None:
    credential = tmp_path / "opaque.env"
    credential.write_text(f"{key}=synthetic\n", encoding="utf-8")

    with pytest.raises(CredentialExecError):
        load_credential_environment((credential,), base={}, policy="compatibility")


def test_compatibility_policy_allows_task_minimal_secret_and_identity_keys(tmp_path: Path) -> None:
    credential = tmp_path / "opaque.env"
    credential.write_text("TELEGRAM_BOT_TOKEN=synthetic\nNATE_OS_AGENT_ID=synthetic-id\n", encoding="utf-8")

    result = load_credential_environment((credential,), base={}, policy="compatibility")

    assert result == {"TELEGRAM_BOT_TOKEN": "synthetic", "NATE_OS_AGENT_ID": "synthetic-id"}
