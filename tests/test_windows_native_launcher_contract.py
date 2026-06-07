# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import json
import os
import platform

import pytest

from skuldbot_runner.windows_native_launcher import (
    WindowsNativeLauncher,
    WindowsNativeLauncherError,
    WindowsNativeLaunchRequest,
    build_parser,
    request_from_args,
)


def _request(**overrides) -> WindowsNativeLaunchRequest:
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
    return WindowsNativeLaunchRequest(**values)


def test_windows_native_launcher_payload_is_refs_only():
    launcher = WindowsNativeLauncher(
        platform_system="Windows",
        environment={"SKULDBOT_WINDOWS_NATIVE_LAUNCHER_PIPE": r"\\.\pipe\skuld-test"},
    )

    payload = launcher.build_request_payload(_request())

    assert payload == {
        "protocolVersion": 1,
        "runtimePlane": "windows_interactive",
        "sessionId": "session-1",
        "robotUserRef": "robot-user-ref-1",
        "credentialRefKey": "vault-key-1",
        "profileRef": "profile-ref-1",
        "tempRootRef": "temp-ref-1",
        "downloadsRootRef": "downloads-ref-1",
        "command": ["python", "-m", "skuldbot_runner.runtime_worker"],
    }
    serialized = json.dumps(payload).lower()
    assert "password" not in serialized
    assert "secret" not in serialized
    assert "token" not in serialized


def test_windows_native_launcher_rejects_non_windows_host():
    launcher = WindowsNativeLauncher(platform_system="Linux")

    try:
        launcher.run(_request())
        raise AssertionError("native launcher should require Windows")
    except WindowsNativeLauncherError as exc:
        assert "requires a Windows host" in str(exc)


def test_windows_native_launcher_rejects_missing_pipe():
    launcher = WindowsNativeLauncher(
        platform_system="Windows",
        environment={"SKULDBOT_WINDOWS_NATIVE_LAUNCHER_PIPE": ""},
    )

    try:
        launcher.run(_request())
        raise AssertionError("native launcher should require a pipe")
    except WindowsNativeLauncherError as exc:
        assert "pipe is not configured" in str(exc)


def test_windows_native_launcher_rejects_invalid_request():
    launcher = WindowsNativeLauncher(platform_system="Windows")

    try:
        launcher.build_request_payload(_request(command=[]))
        raise AssertionError("native launcher should require a command")
    except WindowsNativeLauncherError as exc:
        assert "requires a worker command" in str(exc)


def test_windows_native_launcher_calls_transport_and_returns_exit_code():
    captured: dict[str, object] = {}

    def transport(pipe_name: str, payload: dict):
        captured["pipe"] = pipe_name
        captured["payload"] = payload
        return {"accepted": True, "exitCode": 0}

    launcher = WindowsNativeLauncher(
        platform_system="Windows",
        environment={"SKULDBOT_WINDOWS_NATIVE_LAUNCHER_PIPE": r"\\.\pipe\skuld-test"},
        transport=transport,
    )

    exit_code = launcher.run(_request())

    assert exit_code == 0
    assert captured["pipe"] == r"\\.\pipe\skuld-test"
    assert captured["payload"]["sessionId"] == "session-1"


def test_windows_native_launcher_rejects_service_denial():
    def transport(_pipe_name: str, _payload: dict):
        return {"accepted": False, "reason": "session is locked"}

    launcher = WindowsNativeLauncher(
        platform_system="Windows",
        transport=transport,
    )

    try:
        launcher.run(_request())
        raise AssertionError("native launcher should reject service denial")
    except WindowsNativeLauncherError as exc:
        assert "session is locked" in str(exc)


def test_windows_native_launcher_rejects_non_boolean_accepted_response():
    for accepted in ("false", 1, None):
        def transport(_pipe_name: str, _payload: dict):
            return {"accepted": accepted, "exitCode": 0}

        launcher = WindowsNativeLauncher(
            platform_system="Windows",
            transport=transport,
        )

        try:
            launcher.run(_request())
            raise AssertionError(f"native launcher should reject accepted={accepted!r}")
        except WindowsNativeLauncherError as exc:
            assert "rejected request" in str(exc)


def test_windows_native_launcher_rejects_missing_exit_code():
    def transport(_pipe_name: str, _payload: dict):
        return {"accepted": True}

    launcher = WindowsNativeLauncher(
        platform_system="Windows",
        transport=transport,
    )

    try:
        launcher.run(_request())
        raise AssertionError("native launcher should require exitCode")
    except WindowsNativeLauncherError as exc:
        assert "exitCode" in str(exc)


def test_windows_native_launcher_rejects_non_integer_exit_code_response():
    for exit_code in (False, True, 0.0):
        def transport(_pipe_name: str, _payload: dict):
            return {"accepted": True, "exitCode": exit_code}

        launcher = WindowsNativeLauncher(
            platform_system="Windows",
            transport=transport,
        )

        try:
            launcher.run(_request())
            raise AssertionError(f"native launcher should reject exitCode={exit_code!r}")
        except WindowsNativeLauncherError as exc:
            assert "exitCode" in str(exc)


def test_windows_native_launcher_cli_request_keeps_separator_out_of_worker_command():
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


def test_windows_native_launcher_real_service_integration_env_gated():
    if os.environ.get("SKULDBOT_WINDOWS_NATIVE_LAUNCHER_INTEGRATION") != "1":
        pytest.skip("Set SKULDBOT_WINDOWS_NATIVE_LAUNCHER_INTEGRATION=1 on Windows.")
    if platform.system().lower() != "windows":
        pytest.skip("Windows native launcher integration requires a Windows host.")

    command_json = os.environ.get("SKULDBOT_WINDOWS_NATIVE_TEST_COMMAND", "").strip()
    if not command_json:
        pytest.skip("Set SKULDBOT_WINDOWS_NATIVE_TEST_COMMAND to a JSON command array.")

    command = json.loads(command_json)
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise AssertionError("SKULDBOT_WINDOWS_NATIVE_TEST_COMMAND must be a string array.")

    request = WindowsNativeLaunchRequest(
        session_id=os.environ.get("SKULDBOT_WINDOWS_NATIVE_TEST_SESSION_ID", ""),
        robot_user_ref=os.environ.get("SKULDBOT_WINDOWS_NATIVE_TEST_ROBOT_USER_REF", ""),
        credential_ref_key=os.environ.get(
            "SKULDBOT_WINDOWS_NATIVE_TEST_CREDENTIAL_REF_KEY", ""
        ),
        profile_ref=os.environ.get("SKULDBOT_WINDOWS_NATIVE_TEST_PROFILE_REF"),
        temp_root_ref=os.environ.get("SKULDBOT_WINDOWS_NATIVE_TEST_TEMP_ROOT_REF"),
        downloads_root_ref=os.environ.get("SKULDBOT_WINDOWS_NATIVE_TEST_DOWNLOADS_ROOT_REF"),
        command=command,
    )

    exit_code = WindowsNativeLauncher().run(request)

    assert exit_code == 0
