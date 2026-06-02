# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Graphical runtime capability detection for runner registration."""

from __future__ import annotations

import os
import platform
from collections.abc import Mapping
from dataclasses import dataclass

from .models import (
    DisplayResolution,
    GraphicalDisplayState,
    GraphicalDisplayStateInfo,
    GraphicalRunnerCapabilities,
    GraphicalRuntimePlane,
    GraphicalSessionMode,
    VisualActionKind,
)

DEFAULT_DISPLAY_WIDTH = 1024
DEFAULT_DISPLAY_HEIGHT = 768
DEFAULT_DPI_SCALE = 1.0
DEFAULT_STALE_AFTER_SECONDS = 30

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_WINDOWS_INTERACTIVE_SESSIONS = ("console", "rdp-tcp")


@dataclass(frozen=True)
class GraphicalProbeInput:
    """Pure input used to derive a graphical capability declaration."""

    platform_system: str
    environment: Mapping[str, str]
    screen_width: int | None = None
    screen_height: int | None = None
    dpi_scale: float | None = None


def detect_graphical_capabilities() -> GraphicalRunnerCapabilities | None:
    """Detect this process' graphical capability from local host signals."""

    return build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system=platform.system(),
            environment=os.environ,
        ),
    )


def build_graphical_capabilities(
    probe: GraphicalProbeInput,
) -> GraphicalRunnerCapabilities | None:
    """Build a fail-closed capability declaration from explicit display signals."""

    env = {key.upper(): value for key, value in probe.environment.items()}
    if _read_bool(env.get("SKULDBOT_GRAPHICAL_DISABLED")):
        return None

    plane = _detect_runtime_plane(probe.platform_system, env)
    if plane is None:
        return None

    locked = _read_bool(env.get("SKULDBOT_DISPLAY_LOCKED"))
    if locked:
        state = GraphicalDisplayState.LOCKED
        connected = True
    else:
        state = GraphicalDisplayState.AVAILABLE
        connected = True

    supported_modes = _supported_modes_for(plane, env)
    if not supported_modes:
        return None

    return GraphicalRunnerCapabilities(
        has_display=not locked,
        display=GraphicalDisplayStateInfo(
            state=state,
            locked=locked,
            connected=connected,
            resolution=DisplayResolution(
                width=probe.screen_width or _read_int(env.get("SKULDBOT_DISPLAY_WIDTH"))
                or DEFAULT_DISPLAY_WIDTH,
                height=probe.screen_height
                or _read_int(env.get("SKULDBOT_DISPLAY_HEIGHT"))
                or DEFAULT_DISPLAY_HEIGHT,
            ),
            dpi_scale=probe.dpi_scale
            or _read_float(env.get("SKULDBOT_DISPLAY_DPI_SCALE"))
            or DEFAULT_DPI_SCALE,
            stale_after_seconds=_read_int(env.get("SKULDBOT_DISPLAY_STALE_SECONDS"))
            or DEFAULT_STALE_AFTER_SECONDS,
        ),
        supported_runtime_planes=[plane],
        supported_session_modes=supported_modes,
        supported_visual_actions=[
            VisualActionKind.SCREENSHOT,
            VisualActionKind.WAIT_IMAGE,
            VisualActionKind.IMAGE_CLICK,
            VisualActionKind.OCR_REGION,
            VisualActionKind.ASSERT_TEXT,
            VisualActionKind.TYPE_TEXT,
            VisualActionKind.HOTKEY,
        ],
        max_graphical_sessions=1,
        current_graphical_sessions=0,
        installed_systems=_installed_systems(env),
    )


def _detect_runtime_plane(
    platform_system: str,
    env: Mapping[str, str],
) -> GraphicalRuntimePlane | None:
    explicit_plane = _read_runtime_plane(env.get("SKULDBOT_GRAPHICAL_RUNTIME_PLANE"))
    if explicit_plane is not None:
        return explicit_plane if _explicit_plane_has_display(explicit_plane, env) else None

    system = platform_system.lower()
    if system == "windows" and _has_windows_interactive_session(env):
        return GraphicalRuntimePlane.WINDOWS_INTERACTIVE

    if system == "linux" and (env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")):
        return GraphicalRuntimePlane.LINUX_VIRTUAL_DISPLAY

    return None


def _explicit_plane_has_display(
    plane: GraphicalRuntimePlane,
    env: Mapping[str, str],
) -> bool:
    if plane == GraphicalRuntimePlane.CITRIX_PUBLISHED_APP:
        return _read_bool(env.get("SKULDBOT_CITRIX_SESSION")) and _has_any_display(env)

    if plane == GraphicalRuntimePlane.REMOTE_DESKTOP:
        return _has_windows_interactive_session(env)

    if plane == GraphicalRuntimePlane.WINDOWS_INTERACTIVE:
        return _has_windows_interactive_session(env)

    if plane == GraphicalRuntimePlane.LINUX_VIRTUAL_DISPLAY:
        return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))

    return False


def _has_any_display(env: Mapping[str, str]) -> bool:
    return bool(
        env.get("DISPLAY")
        or env.get("WAYLAND_DISPLAY")
        or _has_windows_interactive_session(env)
    )


def _has_windows_interactive_session(env: Mapping[str, str]) -> bool:
    session = env.get("SESSIONNAME", "").strip().lower()
    if not session:
        return False

    if session == "services":
        return False

    return session.startswith(_WINDOWS_INTERACTIVE_SESSIONS)


def _supported_modes_for(
    plane: GraphicalRuntimePlane,
    env: Mapping[str, str],
) -> list[GraphicalSessionMode]:
    if plane == GraphicalRuntimePlane.WINDOWS_INTERACTIVE:
        return [GraphicalSessionMode.ATTENDED]

    if plane == GraphicalRuntimePlane.REMOTE_DESKTOP:
        return [GraphicalSessionMode.ATTENDED]

    if plane == GraphicalRuntimePlane.CITRIX_PUBLISHED_APP:
        return [GraphicalSessionMode.ATTENDED]

    if plane == GraphicalRuntimePlane.LINUX_VIRTUAL_DISPLAY:
        if _read_bool(env.get("SKULDBOT_GRAPHICAL_UNATTENDED")):
            return [GraphicalSessionMode.UNATTENDED]
        return [GraphicalSessionMode.ATTENDED]

    return []


def _installed_systems(env: Mapping[str, str]) -> list[str]:
    raw = env.get("SKULDBOT_INSTALLED_SYSTEMS", "")
    systems = [item.strip() for item in raw.split(",") if item.strip()]
    return sorted(set(systems))


def _read_runtime_plane(value: str | None) -> GraphicalRuntimePlane | None:
    if not value:
        return None

    try:
        return GraphicalRuntimePlane(value.strip())
    except ValueError:
        return None


def _read_bool(value: str | None) -> bool:
    return bool(value and value.strip().lower() in _TRUE_VALUES)


def _read_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _read_float(value: str | None) -> float | None:
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
