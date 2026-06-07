# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import json
import os
import platform

import pytest

from skuldbot_runner.windows_session_broker import (
    WindowsSessionBroker,
    WindowsSessionBrokerError,
    WindowsSessionBrokerRequest,
    build_parser,
    request_from_args,
)


def _environment(*, launcher: str = "native-launcher") -> dict[str, str]:
    return {
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED": "true",
        "SKULDBOT_WINDOWS_SESSION_NATIVE_LAUNCHER_COMMAND": launcher,
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_JSON": json.dumps(
            [
                {
                    "sessionId": "session-1",
                    "robotUserRef": "robot-user-ref-1",
                    "credentialRefKey": "vault-key-1",
                    "isolation": {
                        "kind": "dedicated_user_session",
                        "inputIsolated": True,
                        "clipboardIsolated": True,
                        "profileRef": "profile-ref-1",
                        "tempRootRef": "temp-ref-1",
                        "downloadsRootRef": "downloads-ref-1",
                    },
                }
            ]
        ),
    }


def _request(**overrides) -> WindowsSessionBrokerRequest:
    values = {
        "session_id": "session-1",
        "robot_user_ref": "robot-user-ref-1",
        "credential_ref_key": "vault-key-1",
        "profile_ref": "profile-ref-1",
        "temp_root_ref": "temp-ref-1",
        "downloads_root_ref": "downloads-ref-1",
        "command": ["python", "-m", "skuldbot_runner.runtime_worker"],
    }
    values.update(overrides)
    return WindowsSessionBrokerRequest(**values)


def test_windows_session_broker_builds_native_launcher_command_refs_only():
    broker = WindowsSessionBroker(
        environment=_environment(),
        platform_system="Windows",
    )

    command = broker.build_launcher_command(_request())

    assert command == [
        "native-launcher",
        "--session-id",
        "session-1",
        "--robot-user-ref",
        "robot-user-ref-1",
        "--credential-ref-key",
        "vault-key-1",
        "--profile-ref",
        "profile-ref-1",
        "--temp-root-ref",
        "temp-ref-1",
        "--downloads-root-ref",
        "downloads-ref-1",
        "--",
        "python",
        "-m",
        "skuldbot_runner.runtime_worker",
    ]
    assert "password" not in " ".join(command).lower()
    assert "secret" not in " ".join(command).lower()


def test_windows_session_broker_rejects_non_windows_host():
    broker = WindowsSessionBroker(
        environment=_environment(),
        platform_system="Linux",
    )

    try:
        broker.build_launcher_command(_request())
        raise AssertionError("broker should require a Windows host")
    except WindowsSessionBrokerError as exc:
        assert "requires a Windows host" in str(exc)


def test_windows_session_broker_rejects_missing_native_launcher():
    env = _environment(launcher="")
    broker = WindowsSessionBroker(environment=env, platform_system="Windows")

    try:
        broker.build_launcher_command(_request())
        raise AssertionError("broker should require a native launcher")
    except WindowsSessionBrokerError as exc:
        assert "requires a native launcher" in str(exc)


def test_windows_session_broker_rejects_unknown_session():
    broker = WindowsSessionBroker(
        environment=_environment(),
        platform_system="Windows",
    )

    try:
        broker.build_launcher_command(_request(session_id="session-2"))
        raise AssertionError("broker should reject sessions outside the pool")
    except WindowsSessionBrokerError as exc:
        assert "not in the configured session pool" in str(exc)


def test_windows_session_broker_rejects_ref_mismatch():
    broker = WindowsSessionBroker(
        environment=_environment(),
        platform_system="Windows",
    )

    try:
        broker.build_launcher_command(_request(credential_ref_key="other-vault-key"))
        raise AssertionError("broker should reject mismatched refs")
    except WindowsSessionBrokerError as exc:
        assert "refs do not match" in str(exc)


def test_windows_session_broker_rejects_empty_worker_command():
    broker = WindowsSessionBroker(
        environment=_environment(),
        platform_system="Windows",
    )

    try:
        broker.build_launcher_command(_request(command=[]))
        raise AssertionError("broker should reject an empty worker command")
    except WindowsSessionBrokerError as exc:
        assert "requires a worker command" in str(exc)


def test_windows_session_broker_launcher_environment_is_explicit_allowlist():
    env = {
        **_environment(),
        "SKULDBOT_SECRET_DATABASE": "raw-secret",
        "SKULDBOT_API_KEY": "skr_raw_api_key",
        "ACCESS_TOKEN": "raw-token",
        "ENCRYPTION_MASTER_KEY": "MASTER-LEAK",
        "SSH_PRIVATE_KEY": "PRIV-LEAK",
        "SIGNING_KEY": "SIGN-LEAK",
        "GH_PAT": "PAT-LEAK",
        "AWS_ACCESS_KEY_ID": "AKID-LEAK",
        "DATABASE_URL": "postgres://u:SUPERSECRETPW@h/db",
        "SKULDBOT_WINDOWS_SESSION_CREDENTIAL_REF_KEY": "vault-key-1",
        "PATH": "/usr/bin",
        "SystemRoot": "C:\\Windows",
    }
    broker = WindowsSessionBroker(environment=env, platform_system="Windows")

    launcher_env = broker._build_launcher_environment()

    assert "SKULDBOT_SECRET_DATABASE" not in launcher_env
    assert "SKULDBOT_API_KEY" not in launcher_env
    assert "ACCESS_TOKEN" not in launcher_env
    assert "ENCRYPTION_MASTER_KEY" not in launcher_env
    assert "SSH_PRIVATE_KEY" not in launcher_env
    assert "SIGNING_KEY" not in launcher_env
    assert "GH_PAT" not in launcher_env
    assert "AWS_ACCESS_KEY_ID" not in launcher_env
    assert "DATABASE_URL" not in launcher_env
    assert "SKULDBOT_WINDOWS_SESSION_CREDENTIAL_REF_KEY" not in launcher_env
    assert launcher_env["PATH"] == "/usr/bin"
    assert launcher_env["SystemRoot"] == "C:\\Windows"


def test_windows_session_broker_cli_request_keeps_separator_out_of_worker_command():
    parser = build_parser()
    args = parser.parse_args(
        [
            "--session-id",
            "session-1",
            "--robot-user-ref",
            "robot-user-ref-1",
            "--credential-ref-key",
            "vault-key-1",
            "--",
            "python",
            "-m",
            "skuldbot_runner.runtime_worker",
        ]
    )

    request = request_from_args(args)

    assert request.command == ["python", "-m", "skuldbot_runner.runtime_worker"]


def test_windows_session_broker_real_host_integration_env_gated():
    if os.environ.get("SKULDBOT_WINDOWS_BROKER_INTEGRATION") != "1":
        pytest.skip("Set SKULDBOT_WINDOWS_BROKER_INTEGRATION=1 on a Windows host.")
    if platform.system().lower() != "windows":
        pytest.skip("Windows broker integration requires a Windows host.")

    command_json = os.environ.get("SKULDBOT_WINDOWS_BROKER_TEST_COMMAND", "").strip()
    if not command_json:
        pytest.skip("Set SKULDBOT_WINDOWS_BROKER_TEST_COMMAND to a JSON command array.")

    try:
        command = json.loads(command_json)
    except json.JSONDecodeError as exc:
        raise AssertionError("SKULDBOT_WINDOWS_BROKER_TEST_COMMAND must be JSON.") from exc
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise AssertionError("SKULDBOT_WINDOWS_BROKER_TEST_COMMAND must be a string array.")

    request = WindowsSessionBrokerRequest(
        session_id=os.environ.get("SKULDBOT_WINDOWS_BROKER_TEST_SESSION_ID", ""),
        robot_user_ref=os.environ.get("SKULDBOT_WINDOWS_BROKER_TEST_ROBOT_USER_REF", ""),
        credential_ref_key=os.environ.get(
            "SKULDBOT_WINDOWS_BROKER_TEST_CREDENTIAL_REF_KEY", ""
        ),
        profile_ref=os.environ.get("SKULDBOT_WINDOWS_BROKER_TEST_PROFILE_REF"),
        temp_root_ref=os.environ.get("SKULDBOT_WINDOWS_BROKER_TEST_TEMP_ROOT_REF"),
        downloads_root_ref=os.environ.get(
            "SKULDBOT_WINDOWS_BROKER_TEST_DOWNLOADS_ROOT_REF"
        ),
        command=command,
    )

    exit_code = WindowsSessionBroker().run(request)

    assert exit_code == 0
