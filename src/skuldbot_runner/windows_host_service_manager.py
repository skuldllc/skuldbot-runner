# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Windows Service manager for the privileged SkuldBot host service.

This module is intentionally small and conservative: it does not launch workers
itself, does not resolve secrets, and does not accept plaintext credentials. It
only installs/controls the privileged named-pipe host as a managed Windows
Service so the runtime is not dependent on an ad-hoc PowerShell or RunCommand
process.
"""

from __future__ import annotations

import argparse
import platform
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .windows_host_service import PyWin32NamedPipeHost, WindowsHostService
from .windows_native_launcher import _DEFAULT_PIPE_NAME

_SERVICE_NAME = "SkuldBotWindowsHostService"
_SERVICE_DISPLAY_NAME = "SkuldBot Windows Host Service"
_SERVICE_DESCRIPTION = (
    "Privileged local service that launches SkuldBot workers inside assigned "
    "Windows robot sessions through a refs-only named-pipe protocol."
)
_ALLOWED_ACTIONS = {"install", "update", "remove", "start", "stop", "restart", "status", "debug"}
_IS_WINDOWS = platform.system().lower() == "windows"


class WindowsHostServiceManagerError(RuntimeError):
    """Raised when the Windows host service cannot be managed safely."""


if _IS_WINDOWS:
    try:
        import servicemanager
        import win32event
        import win32service
        import win32serviceutil
    except ImportError:  # pragma: no cover - exercised by main() fail-closed path.
        servicemanager = None
        win32event = None
        win32service = None
        win32serviceutil = None
else:  # pragma: no cover - platform guard.
    servicemanager = None
    win32event = None
    win32service = None
    win32serviceutil = None


if win32serviceutil is not None:

    class SkuldBotWindowsHostService(win32serviceutil.ServiceFramework):
        _svc_name_ = _SERVICE_NAME
        _svc_display_name_ = _SERVICE_DISPLAY_NAME
        _svc_description_ = _SERVICE_DESCRIPTION

        def __init__(self, service_args: list[str]) -> None:
            super().__init__(service_args)
            self.stop_event = win32event.CreateEvent(None, 0, 0, None)
            self.host = PyWin32NamedPipeHost(pipe_name=_DEFAULT_PIPE_NAME)

        def SvcStop(self) -> None:  # noqa: N802 - pywin32 service API
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.stop_event)
            self.host.request_stop()

        def SvcDoRun(self) -> None:  # noqa: N802 - pywin32 service API
            servicemanager.LogInfoMsg(f"{_SERVICE_DISPLAY_NAME} starting")
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            self.host.serve_forever(
                WindowsHostService(),
                stop_requested=lambda: win32event.WaitForSingleObject(
                    self.stop_event,
                    0,
                )
                == win32event.WAIT_OBJECT_0,
            )
            servicemanager.LogInfoMsg(f"{_SERVICE_DISPLAY_NAME} stopped")

else:
    SkuldBotWindowsHostService = None


@dataclass(frozen=True)
class WindowsHostServiceManagerConfig:
    """Validated service-manager configuration."""

    action: str
    pipe_name: str = _DEFAULT_PIPE_NAME


def parse_manager_config(args: argparse.Namespace) -> WindowsHostServiceManagerConfig:
    """Validate CLI args without importing pywin32 or touching SCM."""

    action = _read_action(args.action)
    pipe_name = _read_pipe_name(args.pipe)
    return WindowsHostServiceManagerConfig(action=action, pipe_name=pipe_name)


def build_service_module_arguments(config: WindowsHostServiceManagerConfig) -> list[str]:
    """Build refs-only service arguments passed to the managed service process."""

    if config.pipe_name != _DEFAULT_PIPE_NAME:
        raise WindowsHostServiceManagerError(
            "Custom Windows host service pipes require an orchestrator-managed profile."
        )
    return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="skuldbot-windows-host-service-manager",
        description="Install or control the SkuldBot privileged Windows host service.",
    )
    parser.add_argument("action", choices=sorted(_ALLOWED_ACTIONS))
    parser.add_argument("--pipe", default=_DEFAULT_PIPE_NAME)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = parse_manager_config(args)
        if platform.system().lower() != "windows":
            raise WindowsHostServiceManagerError(
                "Windows host service manager requires Windows."
            )
        if win32serviceutil is None or SkuldBotWindowsHostService is None:
            raise WindowsHostServiceManagerError(
                "pywin32 is required to manage the Windows host service."
            )
        return _run_pywin32_service_command(config)
    except WindowsHostServiceManagerError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _run_pywin32_service_command(config: WindowsHostServiceManagerConfig) -> int:
    """Run a pywin32 service command.

    Imports are intentionally local so non-Windows runners can import and test
    the contract without pywin32 installed.
    """

    command_argv = [sys.argv[0]]
    if config.action in {"install", "update"}:
        command_argv.extend(
            [
                "--startup",
                "auto",
                "--description",
                _SERVICE_DESCRIPTION,
            ]
        )
    command_argv.extend([*build_service_module_arguments(config), config.action])
    original_argv = sys.argv
    try:
        sys.argv = command_argv
        win32serviceutil.HandleCommandLine(SkuldBotWindowsHostService)
        return 0
    finally:
        sys.argv = original_argv


def _read_action(value: Any) -> str:
    if not isinstance(value, str) or value not in _ALLOWED_ACTIONS:
        raise WindowsHostServiceManagerError("Unsupported Windows host service action.")
    return value


def _read_pipe_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WindowsHostServiceManagerError("Windows host service pipe is required.")
    cleaned = value.strip()
    if "password" in cleaned.lower() or "secret" in cleaned.lower() or "token" in cleaned.lower():
        raise WindowsHostServiceManagerError("Windows host service pipe must not contain secrets.")
    return cleaned


if __name__ == "__main__":
    raise SystemExit(main())
