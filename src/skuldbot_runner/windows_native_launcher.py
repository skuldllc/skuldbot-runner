# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Client for the privileged Windows session launcher service.

The runner process must not attach to another user's interactive desktop directly.
On Windows hosts, this CLI sends a refs-only launch request to a privileged local
service through a named pipe. If that service is missing or rejects the request,
the launch fails closed.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_DEFAULT_PIPE_NAME = r"\\.\pipe\skuldbot-windows-session-launcher"
_PIPE_ENV_KEY = "SKULDBOT_WINDOWS_NATIVE_LAUNCHER_PIPE"
_PROTOCOL_VERSION = 1


class WindowsNativeLauncherError(RuntimeError):
    """Raised when a Windows native launch request cannot run safely."""


@dataclass(frozen=True)
class WindowsNativeLaunchRequest:
    """Refs-only request to execute a worker inside one Windows session."""

    session_id: str
    robot_user_ref: str
    credential_ref_key: str
    command: list[str]
    profile_ref: str | None = None
    temp_root_ref: str | None = None
    downloads_root_ref: str | None = None


Transport = Callable[[str, dict[str, Any]], dict[str, Any]]


class WindowsNativeLauncher:
    """Send launch requests to the privileged Windows host service."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        platform_system: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.environment = dict(environment if environment is not None else os.environ)
        self.platform_system = (platform_system or platform.system()).lower()
        self.transport = transport or self._named_pipe_transport

    def build_request_payload(self, request: WindowsNativeLaunchRequest) -> dict[str, Any]:
        """Build a refs-only protocol payload for the host service."""

        self._validate_request(request)
        return {
            "protocolVersion": _PROTOCOL_VERSION,
            "runtimePlane": "windows_interactive",
            "sessionId": request.session_id,
            "robotUserRef": request.robot_user_ref,
            "credentialRefKey": request.credential_ref_key,
            "profileRef": request.profile_ref or None,
            "tempRootRef": request.temp_root_ref or None,
            "downloadsRootRef": request.downloads_root_ref or None,
            "command": list(request.command),
        }

    def run(self, request: WindowsNativeLaunchRequest) -> int:
        """Submit a launch request and return the worker exit code."""

        if self.platform_system != "windows":
            raise WindowsNativeLauncherError(
                "Windows native launcher requires a Windows host."
            )

        pipe_name = self.environment.get(_PIPE_ENV_KEY, _DEFAULT_PIPE_NAME).strip()
        if not pipe_name:
            raise WindowsNativeLauncherError("Windows native launcher pipe is not configured.")

        payload = self.build_request_payload(request)
        response = self.transport(pipe_name, payload)
        if not bool(response.get("accepted")):
            reason = _read_string(response.get("reason")) or "request rejected"
            raise WindowsNativeLauncherError(f"Windows native launcher rejected request: {reason}")

        exit_code = response.get("exitCode")
        if not isinstance(exit_code, int):
            raise WindowsNativeLauncherError(
                "Windows native launcher response did not include an exitCode."
            )
        return exit_code

    def _validate_request(self, request: WindowsNativeLaunchRequest) -> None:
        if (
            not request.session_id.strip()
            or not request.robot_user_ref.strip()
            or not request.credential_ref_key.strip()
        ):
            raise WindowsNativeLauncherError(
                "Windows native launcher requires session and credential refs."
            )
        if not request.command:
            raise WindowsNativeLauncherError("Windows native launcher requires a worker command.")
        if not all(isinstance(item, str) and item.strip() for item in request.command):
            raise WindowsNativeLauncherError(
                "Windows native launcher worker command must contain nonempty strings."
            )

    @staticmethod
    def _named_pipe_transport(pipe_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_line = json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n"
        try:
            with open(pipe_name, "r+b", buffering=0) as pipe:
                pipe.write(request_line.encode("utf-8"))
                response_line = pipe.readline().decode("utf-8", errors="replace")
        except OSError as exc:
            raise WindowsNativeLauncherError(
                "Windows native launcher service is not reachable."
            ) from exc

        try:
            response = json.loads(response_line)
        except json.JSONDecodeError as exc:
            raise WindowsNativeLauncherError(
                "Windows native launcher service returned invalid JSON."
            ) from exc
        if not isinstance(response, dict):
            raise WindowsNativeLauncherError(
                "Windows native launcher service returned an invalid response."
            )
        return response


def request_from_args(args: argparse.Namespace) -> WindowsNativeLaunchRequest:
    """Create a launch request from parsed CLI arguments."""

    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]

    return WindowsNativeLaunchRequest(
        session_id=args.session_id,
        robot_user_ref=args.robot_user_ref,
        credential_ref_key=args.credential_ref_key,
        profile_ref=args.profile_ref,
        temp_root_ref=args.temp_root_ref,
        downloads_root_ref=args.downloads_root_ref,
        command=command,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the native launcher CLI parser."""

    parser = argparse.ArgumentParser(
        prog="skuldbot-windows-native-launcher",
        description="Send a refs-only Windows session launch request to the host service.",
    )
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--robot-user-ref", required=True)
    parser.add_argument("--credential-ref-key", required=True)
    parser.add_argument("--profile-ref")
    parser.add_argument("--temp-root-ref")
    parser.add_argument("--downloads-root-ref")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""

    parser = build_parser()
    args = parser.parse_args(argv)
    request = request_from_args(args)

    try:
        return WindowsNativeLauncher().run(request)
    except WindowsNativeLauncherError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _read_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


if __name__ == "__main__":
    raise SystemExit(main())
