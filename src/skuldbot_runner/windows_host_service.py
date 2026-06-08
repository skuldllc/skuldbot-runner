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
import ctypes
import json
import os
import platform
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Protocol

from .windows_native_launcher import _DEFAULT_PIPE_NAME, _WORKER_ENV_ALLOWLIST

_PROTOCOL_VERSION = 1
_WINDOWS_INTERACTIVE = "windows_interactive"
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_ATTACHED_SESSION_ENV = "SKULDBOT_WINDOWS_SESSION_ATTACHED"
_WORKER_SYSTEM_ENV_ALLOWLIST = {
    "ALLUSERSPROFILE",
    "COMSPEC",
    "PATH",
    "PATHEXT",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "WINDIR",
}


class WindowsHostServiceError(RuntimeError):
    """Raised when a host-service request cannot be handled safely."""


class _CtypesStartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _CtypesProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


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
    worker_environment: dict[str, str] | None = None


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
    """Windows adapter using pywin32 WTSQueryUserToken/CreateProcessAsUser."""

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
            import win32api
            import win32con
            import win32event
            import win32security
            import win32ts
        except ImportError as exc:
            raise WindowsHostServiceError(
                "pywin32 is required for the Windows host service."
            ) from exc

        try:
            self._enable_required_privileges(win32api, win32con, win32security)
            token = win32ts.WTSQueryUserToken(session_id)
            self._verify_session_user(token, credential, win32security)
            primary_token = self._duplicate_primary_token(token, win32con, win32security)
            command_line = subprocess.list2cmdline(request.command)
            process_handle = self._create_process_with_token(
                int(primary_token),
                command_line,
                request.worker_environment or {},
            )
            wait_result = win32event.WaitForSingleObject(
                process_handle,
                self.timeout_seconds * 1000,
            )
            if wait_result == win32con.WAIT_TIMEOUT:
                raise WindowsHostServiceError("Windows worker process timed out.")
            exit_code = self._get_process_exit_code(process_handle)
            if type(exit_code) is not int:
                raise WindowsHostServiceError("Windows worker exit code was invalid.")
            return exit_code
        except WindowsHostServiceError:
            raise
        except Exception as exc:
            raise WindowsHostServiceError(
                f"Windows process launch failed: {exc}"
            ) from exc

    @staticmethod
    def _verify_session_user(
        token: Any,
        credential: WindowsRobotCredential,
        win32security: Any,
    ) -> None:
        """Ensure the assigned Windows session belongs to the expected robot user."""

        token_user = win32security.GetTokenInformation(token, win32security.TokenUser)
        account_name, domain_name, _account_type = win32security.LookupAccountSid(
            None,
            token_user[0],
        )
        if not session_user_matches_credential(
            account_name=account_name,
            domain_name=domain_name,
            credential=credential,
        ):
            raise WindowsHostServiceError("Windows session user does not match credentialRef.")

    @staticmethod
    def _enable_required_privileges(win32api: Any, win32con: Any, win32security: Any) -> None:
        """Enable Windows privileges required to spawn workers in assigned sessions."""

        process_token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY,
        )
        required_privileges = (
            "SeImpersonatePrivilege",
            "SeAssignPrimaryTokenPrivilege",
            "SeIncreaseQuotaPrivilege",
            "SeTcbPrivilege",
        )
        adjustments = []
        for privilege_name in required_privileges:
            luid = win32security.LookupPrivilegeValue(None, privilege_name)
            adjustments.append((luid, win32con.SE_PRIVILEGE_ENABLED))
        win32security.AdjustTokenPrivileges(process_token, False, adjustments)

    @staticmethod
    def _duplicate_primary_token(token: Any, win32con: Any, win32security: Any) -> Any:
        """Duplicate the WTS token into a primary token suitable for process creation."""

        required_access = (
            win32con.TOKEN_ASSIGN_PRIMARY
            | win32con.TOKEN_DUPLICATE
            | win32con.TOKEN_QUERY
            | win32con.TOKEN_ADJUST_DEFAULT
            | win32con.TOKEN_ADJUST_SESSIONID
        )
        return win32security.DuplicateTokenEx(
            token,
            win32security.SecurityImpersonation,
            required_access,
            win32security.TokenPrimary,
        )

    @staticmethod
    def _create_process_with_token(
        token_handle: int,
        command_line: str,
        worker_environment: Mapping[str, str],
    ) -> int:
        """Launch one command in the assigned session using advapi32.

        pywin32 does not expose the flags Skuld needs consistently. The host service uses
        the session token returned by WTSQueryUserToken and never passes robot
        credentials to the launched worker environment.
        """

        advapi32, kernel32 = _load_windows_process_libraries()
        startup_info = _CtypesStartupInfo()
        startup_info.cb = ctypes.sizeof(startup_info)
        startup_info.lpDesktop = r"winsta0\default"
        process_info = _CtypesProcessInformation()
        mutable_command = ctypes.create_unicode_buffer(command_line)
        environment = _build_worker_environment_block(worker_environment)
        created = advapi32.CreateProcessAsUserW(
            token_handle,
            None,
            mutable_command,
            None,
            None,
            False,
            _CREATE_UNICODE_ENVIRONMENT,
            environment,
            None,
            ctypes.byref(startup_info),
            ctypes.byref(process_info),
        )
        if not created:
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.CloseHandle(process_info.hThread)
        return int(process_info.hProcess)

    @staticmethod
    def _get_process_exit_code(process_handle: int) -> int:
        _advapi32, kernel32 = _load_windows_process_libraries()
        exit_code = ctypes.wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(process_handle, ctypes.byref(exit_code)):
            raise ctypes.WinError(ctypes.get_last_error())
        kernel32.CloseHandle(process_handle)
        return int(exit_code.value)


class PyWin32NamedPipeHost:
    """Named-pipe host for the privileged Windows service."""

    def __init__(self, pipe_name: str = _DEFAULT_PIPE_NAME) -> None:
        self.pipe_name = pipe_name

    def serve_forever(
        self,
        service: WindowsHostService,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        if platform.system().lower() != "windows":
            raise WindowsHostServiceError("Windows named-pipe host requires Windows.")
        try:
            import win32file
            import win32pipe
        except ImportError as exc:
            raise WindowsHostServiceError(
                "pywin32 is required for the Windows named-pipe host."
            ) from exc

        workers: list[threading.Thread] = []
        while not (stop_requested and stop_requested()):
            pipe = win32pipe.CreateNamedPipe(
                self.pipe_name,
                win32pipe.PIPE_ACCESS_DUPLEX,
                win32pipe.PIPE_TYPE_MESSAGE
                | win32pipe.PIPE_READMODE_MESSAGE
                | win32pipe.PIPE_WAIT,
                win32pipe.PIPE_UNLIMITED_INSTANCES,
                65536,
                65536,
                0,
                None,
            )
            try:
                win32pipe.ConnectNamedPipe(pipe, None)
                if stop_requested and stop_requested():
                    win32pipe.DisconnectNamedPipe(pipe)
                    win32file.CloseHandle(pipe)
                    continue
                worker = threading.Thread(
                    target=self._handle_connected_pipe,
                    args=(pipe, service, win32file, win32pipe),
                    daemon=True,
                )
                worker.start()
                workers.append(worker)
                workers = [item for item in workers if item.is_alive()]
            except Exception:
                win32file.CloseHandle(pipe)
                raise

    @staticmethod
    def _handle_connected_pipe(
        pipe: Any,
        service: WindowsHostService,
        win32file: Any,
        win32pipe: Any,
    ) -> None:
        try:
            _, data = win32file.ReadFile(pipe, 65536)
            response = response_from_request_bytes(service, data)
            response_line = json.dumps(response, separators=(",", ":")) + "\n"
            win32file.WriteFile(pipe, response_line.encode("utf-8"))
        finally:
            win32pipe.DisconnectNamedPipe(pipe)
            win32file.CloseHandle(pipe)

    def request_stop(self) -> None:
        """Best-effort wake-up for a service stop request."""

        if platform.system().lower() != "windows":
            return
        try:
            with open(self.pipe_name, "r+b", buffering=0) as pipe:
                pipe.write(b"{}\n")
        except OSError:
            return


def response_from_request_bytes(service: WindowsHostService, data: bytes) -> dict[str, Any]:
    """Parse one named-pipe request and return a strict response."""

    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"accepted": False, "reason": "Windows host service request was invalid JSON."}
    return service.handle_payload(payload)


def _load_windows_process_libraries() -> tuple[Any, Any]:
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.CreateProcessAsUserW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_CtypesStartupInfo),
        ctypes.POINTER(_CtypesProcessInformation),
    ]
    advapi32.CreateProcessAsUserW.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    return advapi32, kernel32


def _read_worker_environment(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise WindowsHostServiceError("Windows worker environment must be an object.")

    cleaned: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise WindowsHostServiceError(
                "Windows worker environment keys and values must be strings."
            )
        if key.upper() not in _WORKER_ENV_ALLOWLIST:
            raise WindowsHostServiceError("Windows worker environment contains an unsupported key.")
        cleaned[key] = item
    return cleaned


def _build_worker_environment_block(worker_environment: Mapping[str, str]) -> Any:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _WORKER_SYSTEM_ENV_ALLOWLIST
    }
    environment.update(worker_environment)
    environment[_ATTACHED_SESSION_ENV] = "1"
    environment_block = "".join(
        f"{key}={value}\0" for key, value in sorted(environment.items())
    )
    return ctypes.create_unicode_buffer(environment_block + "\0")


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
    worker_environment = _read_worker_environment(payload.get("workerEnvironment"))

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
        worker_environment=worker_environment,
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


def session_user_matches_credential(
    *,
    account_name: str,
    domain_name: str,
    credential: WindowsRobotCredential,
) -> bool:
    """Return true when a Windows session identity matches the credential ref."""

    if account_name.lower() != credential.username.lower():
        return False
    if credential.domain and credential.domain != ".":
        return domain_name.lower() == credential.domain.lower()
    return True


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
