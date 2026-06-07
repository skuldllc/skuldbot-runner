# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import os

from skuldbot_runner.executor import BotExecutor
from skuldbot_runner.models import DisplayLease, Job


def _display_lease() -> DisplayLease:
    return DisplayLease.model_validate(
        {
            "leaseId": "lease-1",
            "state": "active",
            "runnerId": "runner-1",
            "grantedAt": "2026-06-03T10:00:00Z",
            "expiresAt": "2026-06-03T10:10:00Z",
            "request": {
                "leaseRequestId": "lease-request-1",
                "tenantId": "tenant-1",
                "runId": "run-1",
                "stepId": "step-1",
                "runtimePlane": "linux_virtual_display",
                "mode": "unattended",
                "requiredCapabilities": ["meditech"],
                "requiredVisualActions": ["screenshot", "type_text"],
                "sessionCredentialRefs": [],
                "reason": "visual runtime",
            },
            "session": {
                "sessionId": "session-1",
                "tenantId": "tenant-1",
                "runnerId": "runner-1",
                "runtimePlane": "linux_virtual_display",
                "mode": "unattended",
                "acquiredAt": "2026-06-03T10:00:00Z",
                "display": {
                    "state": "active",
                    "locked": False,
                    "connected": True,
                    "resolution": {"width": 1280, "height": 720},
                    "dpiScale": 1.0,
                    "staleAfterSeconds": 30,
                },
            },
        }
    )


def test_runtime_worker_environment_is_derived_without_mutating_process_environment():
    executor = BotExecutor.__new__(BotExecutor)
    job = Job(id="run-1", display_lease=_display_lease())

    os.environ.pop("DISPLAY", None)
    env = executor._build_runtime_worker_environment(
        job,
        execution_environment={"DISPLAY": ":101"},
    )

    assert env["DISPLAY"] == ":101"
    assert env["SKULDBOT_DISPLAY_LEASE_ID"] == "lease-1"
    assert os.environ.get("DISPLAY") is None
    assert os.environ.get("SKULDBOT_DISPLAY_LEASE_ID") is None


def test_windows_session_runtime_command_requires_broker_command():
    executor = BotExecutor.__new__(BotExecutor)

    try:
        executor._wrap_windows_session_broker_command(
            ["python", "-m", "skuldbot_runner.runtime_worker"],
            {
                "SKULDBOT_WINDOWS_SESSION_ID": "win-session-1",
                "SKULDBOT_WINDOWS_ROBOT_USER_REF": "robot-user-ref-1",
                "SKULDBOT_WINDOWS_SESSION_CREDENTIAL_REF_KEY": "vault-key-1",
            },
        )
        raise AssertionError("Windows session execution should require a broker command")
    except RuntimeError as exc:
        assert "requires a broker command" in str(exc)


def test_windows_session_runtime_command_requires_refs():
    executor = BotExecutor.__new__(BotExecutor)

    try:
        executor._wrap_windows_session_broker_command(
            ["python", "-m", "skuldbot_runner.runtime_worker"],
            {
                "SKULDBOT_WINDOWS_SESSION_ID": "win-session-1",
                "SKULDBOT_WINDOWS_SESSION_BROKER_COMMAND": "skuldbot-win-broker",
            },
        )
        raise AssertionError("Windows session execution should require refs")
    except RuntimeError as exc:
        assert "requires user and credential refs" in str(exc)


def test_windows_session_runtime_command_wraps_worker_without_plaintext_secret():
    executor = BotExecutor.__new__(BotExecutor)

    command = executor._wrap_windows_session_broker_command(
        ["python", "-m", "skuldbot_runner.runtime_worker"],
        {
            "SKULDBOT_WINDOWS_SESSION_ID": "win-session-1",
            "SKULDBOT_WINDOWS_SESSION_BROKER_COMMAND": "skuldbot-win-broker",
            "SKULDBOT_WINDOWS_ROBOT_USER_REF": "robot-user-ref-1",
            "SKULDBOT_WINDOWS_SESSION_CREDENTIAL_REF_KEY": "vault-key-1",
            "SKULDBOT_WINDOWS_SESSION_PROFILE_REF": "profile-ref-1",
            "SKULDBOT_WINDOWS_SESSION_TEMP_ROOT_REF": "temp-ref-1",
            "SKULDBOT_WINDOWS_SESSION_DOWNLOADS_ROOT_REF": "downloads-ref-1",
        },
    )

    assert command == [
        "skuldbot-win-broker",
        "--session-id",
        "win-session-1",
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
