# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Windows session broker CLI for isolated high-density robot sessions.

This module is intentionally a fail-closed boundary. Python cannot safely attach a
process to another user's interactive Windows desktop by itself without a host
component installed with the right OS privileges. The broker validates Skuld's
session/secret-ref contract, then delegates the actual OS attach to a configured
native launcher command.
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .windows_native_launcher import _WORKER_ENV_ALLOWLIST
from .windows_session_pool import WindowsSessionSlot, slots_from_environment

_NATIVE_LAUNCHER_KEY = "SKULDBOT_WINDOWS_SESSION_NATIVE_LAUNCHER_COMMAND"
_LAUNCHER_ENV_ALLOWLIST = {
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
} | _WORKER_ENV_ALLOWLIST


class WindowsSessionBrokerError(RuntimeError):
    """Raised when a Windows session job cannot be brokered safely."""


@dataclass(frozen=True)
class WindowsSessionBrokerRequest:
    """Validated request to launch a worker inside one Windows robot session."""

    session_id: str
    robot_user_ref: str
    credential_ref_key: str
    command: list[str]
    profile_ref: str | None = None
    temp_root_ref: str | None = None
    downloads_root_ref: str | None = None


class WindowsSessionBroker:
    """Validate Skuld's session contract and call the native Windows launcher."""

    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        platform_system: str | None = None,
    ) -> None:
        self.environment = dict(environment if environment is not None else os.environ)
        self.platform_system = (platform_system or platform.system()).lower()

    def build_launcher_command(self, request: WindowsSessionBrokerRequest) -> list[str]:
        """Build the native launcher command after all fail-closed checks."""

        if self.platform_system != "windows":
            raise WindowsSessionBrokerError(
                "Windows session broker requires a Windows host."
            )

        native_launcher = self.environment.get(_NATIVE_LAUNCHER_KEY, "").strip()
        if not native_launcher:
            raise WindowsSessionBrokerError(
                "Windows session broker requires a native launcher command."
            )

        if not request.command:
            raise WindowsSessionBrokerError(
                "Windows session broker requires a worker command."
            )

        if (
            not request.session_id.strip()
            or not request.robot_user_ref.strip()
            or not request.credential_ref_key.strip()
        ):
            raise WindowsSessionBrokerError(
                "Windows session broker requires session and credential refs."
            )

        slot = self._find_configured_slot(request.session_id)
        if slot is None:
            raise WindowsSessionBrokerError(
                "Windows session broker request is not in the configured session pool."
            )

        if (
            slot.robot_user_ref != request.robot_user_ref
            or slot.credential_ref_key != request.credential_ref_key
        ):
            raise WindowsSessionBrokerError(
                "Windows session broker refs do not match the configured slot."
            )

        return [
            native_launcher,
            "--session-id",
            request.session_id,
            "--robot-user-ref",
            request.robot_user_ref,
            "--credential-ref-key",
            request.credential_ref_key,
            *self._optional_ref_args("--profile-ref", request.profile_ref),
            *self._optional_ref_args("--temp-root-ref", request.temp_root_ref),
            *self._optional_ref_args("--downloads-root-ref", request.downloads_root_ref),
            "--",
            *request.command,
        ]

    def run(self, request: WindowsSessionBrokerRequest) -> int:
        """Execute the native launcher and return its exit code."""

        command = self.build_launcher_command(request)
        completed = subprocess.run(
            command,
            env=self._build_launcher_environment(),
            check=False,
        )
        return int(completed.returncode)

    def _find_configured_slot(self, session_id: str) -> WindowsSessionSlot | None:
        for slot in slots_from_environment(self.environment):
            if slot.session_id == session_id:
                return slot
        return None

    @staticmethod
    def _optional_ref_args(name: str, value: str | None) -> list[str]:
        cleaned = value.strip() if isinstance(value, str) else ""
        return [name, cleaned] if cleaned else []

    def _build_launcher_environment(self) -> dict[str, str]:
        """Return a minimal launcher environment with no inherited secret material."""

        return {
            key: value
            for key, value in self.environment.items()
            if key.upper() in _LAUNCHER_ENV_ALLOWLIST
        }


def request_from_args(args: argparse.Namespace) -> WindowsSessionBrokerRequest:
    """Create a broker request from parsed CLI arguments."""

    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]

    return WindowsSessionBrokerRequest(
        session_id=args.session_id,
        robot_user_ref=args.robot_user_ref,
        credential_ref_key=args.credential_ref_key,
        profile_ref=args.profile_ref,
        temp_root_ref=args.temp_root_ref,
        downloads_root_ref=args.downloads_root_ref,
        command=command,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the broker CLI parser."""

    parser = argparse.ArgumentParser(
        prog="skuldbot-windows-session-broker",
        description="Launch a SkuldBot runtime worker inside an assigned Windows session.",
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
        return WindowsSessionBroker().run(request)
    except WindowsSessionBrokerError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
