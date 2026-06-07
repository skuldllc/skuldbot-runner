# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Privileged Windows host service for assigned robot sessions.

The native launcher client sends a refs-only request to this service. The service
is the only component allowed to resolve the robot credential and call Windows OS
APIs that launch the worker inside the assigned interactive session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .windows_native_launcher import _DEFAULT_PIPE_NAME

_PROTOCOL_VERSION = 1
_WINDOWS_INTERACTIVE = "windows_interactive"


class WindowsHostServiceError(RuntimeError):
    """Raised when a host-service request cannot be handled safely."""


@dataclass(frozen=True)
class WindowsRobotCredential:
    """Credential material resolved inside the privileged service only."""

    username: str
    password: str
    domain: str | None = None


@dataclass(frozen=True)
class WindowsHostLaunchRequest:
    """Validated launch request from the native launcher client."""

    session_id: str
    robot_user_ref: str
    credential_ref_key: str
    command: list[str]
    profile_ref: str | None = None
    temp_root_ref: str | None = None
    downloads_root_ref: str | None = None


class WindowsProcessAdapter(Protocol):
    """Launches one command inside an assigned Windows user session."""

    def launch(
        self,
        request: WindowsHostLaunchRequest,
        credential: WindowsRobotCredential,
    ) -> int:
        """Return the worker process exit code."""


SecretResolver = Callable[[str], str | None]


class WindowsHostService:
    """Validates requests, resolves credential refs, and delegates OS attach."""

    def __init__(
        self,
        *,
        adapter: WindowsProcessAdapter | None = None,
        secret_resolver: SecretResolver | None = None,
        platform_system: str | None = None,
    ) -> None:
        self.platform_system = (platform_system or platform.system()).lower()
        self.adapter = adapter or PyWin32SessionProcessAdapter(
            platform_system=self.platform_system
        )
        self.secret_resolver = secret_resolver or resolve_secret_value

    def handle_payload(self, payload: Any) -> dict[str, Any]:
        """Handle one protocol payload and return a strict service response."""

        try:
            if self.platform_system != "windows":
                raise WindowsHostServiceError("Windows host service requires Windows.")

            request = parse_launch_payload(payload)
            credential = resolve_robot_credential(
                request.credential_ref_key,
                self.secret_resolver,
            )
            exit_code = self.adapter.launch(request, credential)
            if type(exit_code) is not int:
                raise WindowsHostServiceError("Windows process adapter returned invalid exit code.")
            return {"accepted": True, "exitCode": exit_code}
        except WindowsHostServiceError as exc:
            return {"accepted": False, "reason": str(exc)}


class PyWin32SessionProcessAdapter:
    """Windows adapter using pywin32 LogonUser/CreateProcessAsUser."""

    def __init__(self, *, platform_system: str | None = None, timeout_seconds: int = 3600) -> None:
        self.platform_system = (platform_system or platform.system()).lower()
        self.timeout_seconds = timeout_seconds

    def launch(
        self,
        request: WindowsHostLaunchRequest,
        credential: WindowsRobotCredential,
    ) -> int:
        if self.platform_system != "windows":
            raise WindowsHostServiceError("Windows process adapter requires Windows.")

        session_id = _read_positive_int(request.session_id, "sessionId")
        try:
            import win32con
            import win32event
            import win32process
            import win32profile
            import win32security
        except ImportError as exc:
            raise WindowsHostServiceError(
                "pywin32 is required for the Windows host service."
            ) from exc

        domain = credential.domain or "."
        try:
            token = win32security.LogonUser(
                credential.username,
                domain,
                credential.password,
                win32con.LOGON32_LOGON_INTERACTIVE,
                win32con.LOGON32_PROVIDER_DEFAULT,
            )
            primary_token = win32security.DuplicateTokenEx(
                token,
                0,
                win32security.SecurityImpersonation,
                win32security.TokenPrimary,
            )
            win32security.SetTokenInformation(
                primary_token,
                win32security.TokenSessionId,
                session_id,
            )
            environment = win32profile.CreateEnvironmentBlock(primary_token, False)
            startup = win32process.STARTUPINFO()
            startup.lpDesktop = r"winsta0\default"
            command_line = subprocess.list2cmdline(request.command)
            process_info = win32process.CreateProcessAsUser(
                primary_token,
                None,
                command_line,
                None,
                None,
                False,
                win32con.CREATE_UNICODE_ENVIRONMENT,
                environment,
                None,
                startup,
            )
            process_handle = process_info[0]
            wait_result = win32event.WaitForSingleObject(
                process_handle,
                self.timeout_seconds * 1000,
            )
            if wait_result == win32con.WAIT_TIMEOUT:
                raise WindowsHostServiceError("Windows worker process timed out.")
            exit_code = win32process.GetExitCodeProcess(process_handle)
            if type(exit_code) is not int:
                raise WindowsHostServiceError("Windows worker exit code was invalid.")
            return exit_code
        except WindowsHostServiceError:
            raise
        except Exception as exc:
            raise WindowsHostServiceError("Windows process launch failed.") from exc


class PyWin32NamedPipeHost:
    """Named-pipe host for the privileged Windows service."""

    def __init__(self, pipe_name: str = _DEFAULT_PIPE_NAME) -> None:
        self.pipe_name = pipe_name

    def serve_forever(self, service: WindowsHostService) -> None:
        if platform.system().lower() != "windows":
            raise WindowsHostServiceError("Windows named-pipe host requires Windows.")
        try:
            import win32file
            import win32pipe
        except ImportError as exc:
            raise WindowsHostServiceError(
                "pywin32 is required for the Windows named-pipe host."
            ) from exc

        while True:
            pipe = win32pipe.CreateNamedPipe(
                self.pipe_name,
                win32pipe.PIPE_ACCESS_DUPLEX,
                win32pipe.PIPE_TYPE_MESSAGE
                | win32pipe.PIPE_READMODE_MESSAGE
                | win32pipe.PIPE_WAIT,
                1,
                65536,
                65536,
                0,
                None,
            )
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
                _, data = win32file.ReadFile(pipe, 65536)
                response = response_from_request_bytes(service, data)
                response_line = json.dumps(response, separators=(",", ":")) + "\n"
                win32file.WriteFile(pipe, response_line.encode("utf-8"))
            finally:
                win32pipe.DisconnectNamedPipe(pipe)
                win32file.CloseHandle(pipe)


def response_from_request_bytes(service: WindowsHostService, data: bytes) -> dict[str, Any]:
    """Parse one named-pipe request and return a strict response."""

    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"accepted": False, "reason": "Windows host service request was invalid JSON."}
    return service.handle_payload(payload)


def parse_launch_payload(payload: Mapping[str, Any]) -> WindowsHostLaunchRequest:
    """Validate one launcher payload."""

    if not isinstance(payload, Mapping):
        raise WindowsHostServiceError("Windows host service payload must be an object.")

    protocol_version = payload.get("protocolVersion")
    if type(protocol_version) is not int or protocol_version != _PROTOCOL_VERSION:
        raise WindowsHostServiceError("Unsupported Windows host service protocol version.")
    if payload.get("runtimePlane") != _WINDOWS_INTERACTIVE:
        raise WindowsHostServiceError("Windows host service requires windows_interactive plane.")

    session_id = _read_required_string(payload.get("sessionId"), "sessionId")
    robot_user_ref = _read_required_string(payload.get("robotUserRef"), "robotUserRef")
    credential_ref_key = _read_required_string(
        payload.get("credentialRefKey"),
        "credentialRefKey",
    )
    command = payload.get("command")
    if not isinstance(command, list) or not command:
        raise WindowsHostServiceError("Windows host service requires a worker command.")
    if not all(isinstance(item, str) and item.strip() for item in command):
        raise WindowsHostServiceError(
            "Windows host service worker command must contain nonempty strings."
        )

    return WindowsHostLaunchRequest(
        session_id=session_id,
        robot_user_ref=robot_user_ref,
        credential_ref_key=credential_ref_key,
        profile_ref=_read_optional_string(payload.get("profileRef"), "profileRef"),
        temp_root_ref=_read_optional_string(payload.get("tempRootRef"), "tempRootRef"),
        downloads_root_ref=_read_optional_string(
            payload.get("downloadsRootRef"),
            "downloadsRootRef",
        ),
        command=list(command),
    )


def resolve_robot_credential(
    credential_ref_key: str,
    secret_resolver: SecretResolver,
) -> WindowsRobotCredential:
    """Resolve and parse a Windows robot credential secret."""

    secret_value = secret_resolver(credential_ref_key)
    if not secret_value:
        raise WindowsHostServiceError("Windows credentialRef could not be resolved.")

    try:
        payload = json.loads(secret_value)
    except json.JSONDecodeError as exc:
        raise WindowsHostServiceError("Windows credentialRef must resolve to JSON.") from exc
    if not isinstance(payload, dict):
        raise WindowsHostServiceError("Windows credential payload must be an object.")

    username = _read_required_string(payload.get("username"), "username")
    password = _read_required_string(payload.get("password"), "password")
    domain = _read_optional_string(payload.get("domain"), "domain")
    return WindowsRobotCredential(username=username, password=password, domain=domain)


def resolve_secret_value(secret_ref_key: str) -> str | None:
    """Resolve a secretRef through the configured runner secrets manager."""

    try:
        from .secrets.manager import get_secrets_manager
    except ImportError as exc:
        raise WindowsHostServiceError("Runner secrets manager is required.") from exc

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        raise WindowsHostServiceError(
            "Windows credentialRef cannot be resolved inside a running event loop."
        )

    return asyncio.run(get_secrets_manager().get_secret(secret_ref_key))


def build_parser() -> argparse.ArgumentParser:
    """Build the host service CLI parser."""

    parser = argparse.ArgumentParser(
        prog="skuldbot-windows-host-service",
        description="Run the privileged SkuldBot Windows session host service.",
    )
    parser.add_argument("--pipe", default=_DEFAULT_PIPE_NAME)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        PyWin32NamedPipeHost(pipe_name=args.pipe).serve_forever(WindowsHostService())
        return 0
    except WindowsHostServiceError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _read_required_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WindowsHostServiceError(f"{field_name} is required.")
    return value.strip()


def _read_optional_string(value: Any, field_name: str) -> str | None:
    if value is not None and not isinstance(value, str):
        raise WindowsHostServiceError(f"{field_name} must be a string.")
    cleaned = value.strip() if isinstance(value, str) else ""
    return cleaned or None


def _read_positive_int(value: str, field_name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise WindowsHostServiceError(f"{field_name} must be numeric.") from exc
    if parsed < 0:
        raise WindowsHostServiceError(f"{field_name} must be nonnegative.")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
