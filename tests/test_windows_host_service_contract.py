# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import json
import os
import platform

import pytest

from skuldbot_runner.windows_host_service import (
    PyWin32SessionProcessAdapter,
    WindowsHostLaunchRequest,
    WindowsHostService,
    WindowsHostServiceError,
    parse_launch_payload,
    resolve_robot_credential,
    response_from_request_bytes,
)


def _payload(**overrides):
    payload = {
        "protocolVersion": 1,
        "runtimePlane": "windows_interactive",
        "sessionId": "42",
        "robotUserRef": "robot-user-ref-1",
        "credentialRefKey": "vault-key-1",
        "profileRef": "profile-ref-1",
        "tempRootRef": "temp-ref-1",
        "downloadsRootRef": "downloads-ref-1",
        "command": ["python", "-m", "skuldbot_runner.runtime_worker"],
    }
    payload.update(overrides)
    return payload


def _secret_resolver(key: str) -> str | None:
    if key != "vault-key-1":
        return None
    return json.dumps({"username": "robot-user", "password": "secret-password", "domain": "ACME"})


class _CapturingAdapter:
    def __init__(self, exit_code=0):
        self.exit_code = exit_code
        self.request = None
        self.credential = None

    def launch(self, request: WindowsHostLaunchRequest, credential):
        self.request = request
        self.credential = credential
        return self.exit_code


def test_windows_host_service_parses_refs_only_payload():
    request = parse_launch_payload(_payload())

    assert request.session_id == "42"
    assert request.robot_user_ref == "robot-user-ref-1"
    assert request.credential_ref_key == "vault-key-1"
    assert request.profile_ref == "profile-ref-1"
    assert request.temp_root_ref == "temp-ref-1"
    assert request.downloads_root_ref == "downloads-ref-1"
    assert request.command == ["python", "-m", "skuldbot_runner.runtime_worker"]


def test_windows_host_service_rejects_wrong_protocol_and_plane():
    for payload in (
        _payload(protocolVersion=2),
        _payload(protocolVersion=True),
        _payload(runtimePlane="linux_virtual_display"),
    ):
        try:
            parse_launch_payload(payload)
            raise AssertionError("host service should reject invalid protocol payload")
        except WindowsHostServiceError:
            pass


def test_windows_host_service_rejects_non_object_payload():
    service = WindowsHostService(
        adapter=_CapturingAdapter(),
        secret_resolver=_secret_resolver,
        platform_system="Windows",
    )

    response = service.handle_payload(["not", "an", "object"])

    assert response["accepted"] is False
    assert "payload must be an object" in response["reason"]


def test_windows_host_service_rejects_malformed_optional_refs():
    try:
        parse_launch_payload(_payload(profileRef=123))
        raise AssertionError("host service should reject malformed optional refs")
    except WindowsHostServiceError as exc:
        assert "profileRef must be a string" in str(exc)


def test_windows_host_service_rejects_empty_or_malformed_command():
    for command in ([], ["python", ""], "python -m worker"):
        try:
            parse_launch_payload(_payload(command=command))
            raise AssertionError(f"host service should reject command={command!r}")
        except WindowsHostServiceError as exc:
            assert "command" in str(exc)


def test_windows_host_service_resolves_robot_credential_from_secret_ref():
    credential = resolve_robot_credential("vault-key-1", _secret_resolver)

    assert credential.username == "robot-user"
    assert credential.password == "secret-password"
    assert credential.domain == "ACME"


def test_windows_host_service_rejects_unresolved_or_malformed_credential_ref():
    for resolver in (
        lambda _key: None,
        lambda _key: "not-json",
        lambda _key: json.dumps({"username": "robot-user"}),
    ):
        try:
            resolve_robot_credential("vault-key-1", resolver)
            raise AssertionError("host service should reject invalid credential secret")
        except WindowsHostServiceError:
            pass


def test_windows_host_service_handles_payload_without_leaking_password():
    adapter = _CapturingAdapter(exit_code=0)
    service = WindowsHostService(
        adapter=adapter,
        secret_resolver=_secret_resolver,
        platform_system="Windows",
    )

    response = service.handle_payload(_payload())

    assert response == {"accepted": True, "exitCode": 0}
    assert adapter.request.session_id == "42"
    assert adapter.credential.username == "robot-user"
    assert "secret-password" not in json.dumps(response)


def test_windows_host_service_pipe_request_rejects_invalid_json():
    service = WindowsHostService(
        adapter=_CapturingAdapter(),
        secret_resolver=_secret_resolver,
        platform_system="Windows",
    )

    response = response_from_request_bytes(service, b"{not-json")

    assert response["accepted"] is False
    assert "invalid JSON" in response["reason"]


def test_windows_host_service_rejects_non_windows_host():
    service = WindowsHostService(
        adapter=_CapturingAdapter(),
        secret_resolver=_secret_resolver,
        platform_system="Linux",
    )

    response = service.handle_payload(_payload())

    assert response["accepted"] is False
    assert "requires Windows" in response["reason"]


def test_windows_host_service_rejects_adapter_non_integer_exit_code():
    service = WindowsHostService(
        adapter=_CapturingAdapter(exit_code=False),
        secret_resolver=_secret_resolver,
        platform_system="Windows",
    )

    response = service.handle_payload(_payload())

    assert response["accepted"] is False
    assert "exit code" in response["reason"]


def test_pywin32_adapter_rejects_non_windows_host_and_nonnumeric_session():
    adapter = PyWin32SessionProcessAdapter(platform_system="Linux")

    try:
        adapter.launch(
            WindowsHostLaunchRequest(
                session_id="42",
                robot_user_ref="robot-user-ref-1",
                credential_ref_key="vault-key-1",
                command=["python"],
            ),
            resolve_robot_credential("vault-key-1", _secret_resolver),
        )
        raise AssertionError("adapter should require Windows")
    except WindowsHostServiceError as exc:
        assert "requires Windows" in str(exc)

    adapter = PyWin32SessionProcessAdapter(platform_system="Windows")
    try:
        adapter.launch(
            WindowsHostLaunchRequest(
                session_id="not-numeric",
                robot_user_ref="robot-user-ref-1",
                credential_ref_key="vault-key-1",
                command=["python"],
            ),
            resolve_robot_credential("vault-key-1", _secret_resolver),
        )
        raise AssertionError("adapter should require numeric Windows session id")
    except WindowsHostServiceError as exc:
        assert "sessionId must be numeric" in str(exc)


def test_windows_host_service_real_attach_integration_env_gated():
    if os.environ.get("SKULDBOT_WINDOWS_HOST_SERVICE_INTEGRATION") != "1":
        pytest.skip("Set SKULDBOT_WINDOWS_HOST_SERVICE_INTEGRATION=1 on Windows.")
    if platform.system().lower() != "windows":
        pytest.skip("Windows host service integration requires a Windows host.")

    command_json = os.environ.get("SKULDBOT_WINDOWS_HOST_TEST_COMMAND", "").strip()
    if not command_json:
        pytest.skip("Set SKULDBOT_WINDOWS_HOST_TEST_COMMAND to a JSON command array.")

    command = json.loads(command_json)
    if not isinstance(command, list) or not all(isinstance(item, str) for item in command):
        raise AssertionError("SKULDBOT_WINDOWS_HOST_TEST_COMMAND must be a string array.")

    payload = _payload(
        sessionId=os.environ.get("SKULDBOT_WINDOWS_HOST_TEST_SESSION_ID", ""),
        robotUserRef=os.environ.get("SKULDBOT_WINDOWS_HOST_TEST_ROBOT_USER_REF", ""),
        credentialRefKey=os.environ.get(
            "SKULDBOT_WINDOWS_HOST_TEST_CREDENTIAL_REF_KEY",
            "",
        ),
        command=command,
    )

    response = WindowsHostService().handle_payload(payload)

    assert response == {"accepted": True, "exitCode": 0}
