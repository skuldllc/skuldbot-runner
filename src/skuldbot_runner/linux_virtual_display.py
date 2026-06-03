# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Linux Xvfb display lifecycle for the linux_virtual_display runtime plane."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass


class LinuxVirtualDisplayError(RuntimeError):
    """Raised when the Linux virtual display cannot be started safely."""


@dataclass(frozen=True)
class LinuxVirtualDisplayConfig:
    """Configuration used to start an Xvfb display."""

    display: str = ":99"
    width: int = 1280
    height: int = 720
    depth: int = 24
    startup_timeout_seconds: float = 3.0

    @property
    def screen_geometry(self) -> str:
        return f"{self.width}x{self.height}x{self.depth}"


def should_start_linux_virtual_display(
    environment: Mapping[str, str] | None = None,
    platform_system: str | None = None,
) -> bool:
    """Return true only when Xvfb was explicitly requested and no display exists."""

    env = environment or os.environ
    system = (platform_system or platform.system()).lower()
    if system != "linux":
        return False

    if not _read_bool(env.get("SKULDBOT_LINUX_VIRTUAL_DISPLAY_ENABLED")):
        return False

    return not bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def config_from_environment(
    environment: Mapping[str, str] | None = None,
) -> LinuxVirtualDisplayConfig:
    """Build Xvfb config from runner environment values."""

    env = environment or os.environ
    return LinuxVirtualDisplayConfig(
        display=env.get("SKULDBOT_LINUX_VIRTUAL_DISPLAY", ":99").strip() or ":99",
        width=_read_positive_int(env.get("SKULDBOT_DISPLAY_WIDTH")) or 1280,
        height=_read_positive_int(env.get("SKULDBOT_DISPLAY_HEIGHT")) or 720,
        depth=_read_positive_int(env.get("SKULDBOT_DISPLAY_DEPTH")) or 24,
        startup_timeout_seconds=_read_positive_float(
            env.get("SKULDBOT_LINUX_VIRTUAL_DISPLAY_STARTUP_TIMEOUT_SECONDS")
        )
        or 3.0,
    )


def build_xvfb_command(config: LinuxVirtualDisplayConfig) -> list[str]:
    """Build the Xvfb command line for a virtual display session."""

    return [
        "Xvfb",
        config.display,
        "-screen",
        "0",
        config.screen_geometry,
        "-nolisten",
        "tcp",
    ]


class LinuxVirtualDisplaySession:
    """Owns an Xvfb process and exposes DISPLAY to the runner process."""

    def __init__(
        self,
        config: LinuxVirtualDisplayConfig,
        environment: MutableMapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.environment = environment or os.environ
        self._process: subprocess.Popen[str] | None = None
        self._previous_values: dict[str, str | None] = {}

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> None:
        """Start Xvfb and set DISPLAY for the runner process."""

        if self.is_running:
            return

        if shutil.which("Xvfb") is None:
            raise LinuxVirtualDisplayError("Xvfb executable is not available.")

        managed_keys = (
            "DISPLAY",
            "SKULDBOT_GRAPHICAL_RUNTIME_PLANE",
            "SKULDBOT_GRAPHICAL_UNATTENDED",
        )
        self._previous_values = {key: self.environment.get(key) for key in managed_keys}
        self._process = subprocess.Popen(  # noqa: S603
            build_xvfb_command(self.config),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.environment["DISPLAY"] = self.config.display
        self.environment["SKULDBOT_GRAPHICAL_RUNTIME_PLANE"] = "linux_virtual_display"
        self.environment["SKULDBOT_GRAPHICAL_UNATTENDED"] = "true"

        deadline = time.monotonic() + self.config.startup_timeout_seconds
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                self.stop()
                raise LinuxVirtualDisplayError("Xvfb exited before becoming ready.")
            if self._display_is_ready():
                return
            time.sleep(0.05)

        if not self._display_is_ready():
            self.stop()
            raise LinuxVirtualDisplayError("Xvfb display did not become ready.")

    def stop(self) -> None:
        """Stop Xvfb and restore the previous DISPLAY value."""

        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

        for key, previous_value in self._previous_values.items():
            if previous_value is None:
                self.environment.pop(key, None)
            else:
                self.environment[key] = previous_value
        self._previous_values = {}

    def _display_is_ready(self) -> bool:
        if self._process is None or self._process.poll() is not None:
            return False

        if shutil.which("xdpyinfo") is None:
            return time.monotonic() > 0

        probe_env = dict(os.environ)
        probe_env["DISPLAY"] = self.config.display
        result = subprocess.run(  # noqa: S603
            ["xdpyinfo"],
            env=probe_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0


def _read_bool(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "y", "on"})


def _read_positive_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _read_positive_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
