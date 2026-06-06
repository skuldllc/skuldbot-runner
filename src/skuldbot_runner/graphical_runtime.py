# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Graphical runtime capability detection for runner registration."""

from __future__ import annotations

import json
import os
import platform
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .models import (
    DisplayLease,
    DisplayLeaseState,
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
_WINDOWS_SESSION_POOL_ENV_KEYS = (
    "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_JSON",
    "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL",
)
_WINDOWS_DEDICATED_SESSION_ISOLATION = "dedicated_user_session"
_PLAINTEXT_SECRET_KEYS = {
    "password",
    "passwordvalue",
    "secret",
    "secretvalue",
    "token",
    "tokenvalue",
    "credential",
    "credentialvalue",
}
ACTIVE_DISPLAY_LEASE_STATES = {DisplayLeaseState.GRANTED, DisplayLeaseState.ACTIVE}

LEASE_ENV_LEASE_ID = "SKULDBOT_DISPLAY_LEASE_ID"
LEASE_ENV_RUN_ID = "SKULDBOT_DISPLAY_LEASE_RUN_ID"
LEASE_ENV_STATE = "SKULDBOT_DISPLAY_LEASE_STATE"
LEASE_ENV_RUNTIME_PLANE = "SKULDBOT_DISPLAY_LEASE_RUNTIME_PLANE"
LEASE_ENV_SESSION_MODE = "SKULDBOT_DISPLAY_LEASE_SESSION_MODE"
LEASE_ENV_ACTIONS = "SKULDBOT_DISPLAY_LEASE_ACTIONS"


@dataclass(frozen=True)
class GraphicalProbeInput:
    """Pure input used to derive a graphical capability declaration."""

    platform_system: str
    environment: Mapping[str, str]
    screen_width: int | None = None
    screen_height: int | None = None
    dpi_scale: float | None = None
    max_graphical_sessions: int | None = None
    current_graphical_sessions: int | None = None


@dataclass(frozen=True)
class DisplayLeaseRuntimeContext:
    """Display lease facts read from the runner execution environment."""

    lease_id: str
    run_id: str
    state: DisplayLeaseState
    runtime_plane: GraphicalRuntimePlane
    mode: GraphicalSessionMode
    required_visual_actions: tuple[VisualActionKind, ...]

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_DISPLAY_LEASE_STATES

    def allows(self, action: VisualActionKind) -> bool:
        return action in self.required_visual_actions


def detect_graphical_capabilities() -> GraphicalRunnerCapabilities | None:
    """Detect this process' graphical capability from local host signals."""

    return build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system=platform.system(),
            environment=os.environ,
        ),
    )


def build_display_lease_environment(lease: DisplayLease) -> dict[str, str]:
    """Build environment variables that bind visual keywords to a granted lease."""

    return {
        LEASE_ENV_LEASE_ID: lease.lease_id,
        LEASE_ENV_RUN_ID: lease.request.run_id,
        LEASE_ENV_STATE: lease.state.value,
        LEASE_ENV_RUNTIME_PLANE: lease.request.runtime_plane.value,
        LEASE_ENV_SESSION_MODE: lease.request.mode.value,
        LEASE_ENV_ACTIONS: ",".join(
            action.value for action in lease.request.required_visual_actions
        ),
        "SKULDBOT_GRAPHICAL_RUNTIME_PLANE": lease.request.runtime_plane.value,
    }


def read_display_lease_context(
    environment: Mapping[str, str] | None = None,
) -> DisplayLeaseRuntimeContext | None:
    """Read the active display lease context from process environment variables."""

    env = environment or os.environ
    lease_id = env.get(LEASE_ENV_LEASE_ID, "").strip()
    run_id = env.get(LEASE_ENV_RUN_ID, "").strip()
    state = _read_display_lease_state(env.get(LEASE_ENV_STATE))
    runtime_plane = _read_runtime_plane(env.get(LEASE_ENV_RUNTIME_PLANE))
    mode = _read_session_mode(env.get(LEASE_ENV_SESSION_MODE))
    actions = _read_visual_actions(env.get(LEASE_ENV_ACTIONS))

    if not lease_id or not run_id or state is None or runtime_plane is None or mode is None:
        return None

    return DisplayLeaseRuntimeContext(
        lease_id=lease_id,
        run_id=run_id,
        state=state,
        runtime_plane=runtime_plane,
        mode=mode,
        required_visual_actions=tuple(actions),
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
        max_graphical_sessions=_max_sessions_for(plane, env, probe.max_graphical_sessions),
        current_graphical_sessions=probe.current_graphical_sessions or 0,
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

    if system == "linux" and (
        env.get("DISPLAY")
        or env.get("WAYLAND_DISPLAY")
        or _read_bool(env.get("SKULDBOT_LINUX_VIRTUAL_DISPLAY_ENABLED"))
    ):
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
        return bool(
            env.get("DISPLAY")
            or env.get("WAYLAND_DISPLAY")
            or _read_bool(env.get("SKULDBOT_LINUX_VIRTUAL_DISPLAY_ENABLED"))
        )

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
        if _windows_session_pool_capacity(env) > 0:
            return [GraphicalSessionMode.UNATTENDED]
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


def _max_sessions_for(
    plane: GraphicalRuntimePlane,
    env: Mapping[str, str],
    requested_max: int | None,
) -> int:
    requested = requested_max or _read_int(env.get("SKULDBOT_MAX_GRAPHICAL_SESSIONS")) or 1
    if requested < 1:
        return 1

    if plane == GraphicalRuntimePlane.LINUX_VIRTUAL_DISPLAY:
        if _read_bool(env.get("SKULDBOT_LINUX_VIRTUAL_DISPLAY_SESSION_POOL_ENABLED")):
            return requested
        if _read_bool(env.get("SKULDBOT_LINUX_VIRTUAL_DISPLAY_ENABLED")) and not (
            env.get("DISPLAY") or env.get("WAYLAND_DISPLAY")
        ):
            return requested
        return 1

    if plane == GraphicalRuntimePlane.WINDOWS_INTERACTIVE:
        pool_capacity = _windows_session_pool_capacity(env)
        if pool_capacity > 0:
            return min(requested, pool_capacity)
        return 1

    if plane in {
        GraphicalRuntimePlane.REMOTE_DESKTOP,
        GraphicalRuntimePlane.CITRIX_PUBLISHED_APP,
    }:
        if _read_bool(env.get("SKULDBOT_REMOTE_SESSION_POOL_ENABLED")):
            return requested
        return 1

    return 1


def _installed_systems(env: Mapping[str, str]) -> list[str]:
    raw = env.get("SKULDBOT_INSTALLED_SYSTEMS", "")
    systems = [item.strip() for item in raw.split(",") if item.strip()]
    return sorted(set(systems))


def _windows_session_pool_capacity(env: Mapping[str, str]) -> int:
    if not _read_bool(env.get("SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED")):
        return 0

    raw_pool = next(
        (
            env[key].strip()
            for key in _WINDOWS_SESSION_POOL_ENV_KEYS
            if env.get(key, "").strip()
        ),
        "",
    )
    if not raw_pool:
        return 0

    try:
        parsed = json.loads(raw_pool)
    except json.JSONDecodeError:
        return 0

    if not isinstance(parsed, list):
        return 0

    valid_session_ids = {
        slot["sessionId"].strip()
        for slot in parsed
        if _windows_session_pool_slot_is_valid(slot)
    }
    return len(valid_session_ids)


def _windows_session_pool_slot_is_valid(slot: Any) -> bool:
    if not isinstance(slot, dict):
        return False

    if _contains_plaintext_secret(slot):
        return False

    if not _nonempty_string(slot.get("sessionId")):
        return False
    if not _nonempty_string(slot.get("robotUserRef")):
        return False
    if not _nonempty_string(slot.get("credentialRefKey")):
        return False

    isolation = slot.get("isolation")
    if not isinstance(isolation, dict):
        return False

    if str(isolation.get("kind", "")).strip().lower() != (
        _WINDOWS_DEDICATED_SESSION_ISOLATION
    ):
        return False

    return bool(isolation.get("inputIsolated")) and bool(
        isolation.get("clipboardIsolated")
    )


def _contains_plaintext_secret(slot: Mapping[str, Any]) -> bool:
    for key in slot:
        normalized = "".join(ch for ch in key.lower() if ch.isalnum())
        if normalized == "credentialrefkey":
            continue
        if normalized in _PLAINTEXT_SECRET_KEYS:
            return True
    return False


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _read_runtime_plane(value: str | None) -> GraphicalRuntimePlane | None:
    if not value:
        return None

    try:
        return GraphicalRuntimePlane(value.strip())
    except ValueError:
        return None


def _read_session_mode(value: str | None) -> GraphicalSessionMode | None:
    if not value:
        return None

    try:
        return GraphicalSessionMode(value.strip())
    except ValueError:
        return None


def _read_display_lease_state(value: str | None) -> DisplayLeaseState | None:
    if not value:
        return None

    try:
        return DisplayLeaseState(value.strip())
    except ValueError:
        return None


def _read_visual_actions(value: str | None) -> list[VisualActionKind]:
    if not value:
        return []

    actions: list[VisualActionKind] = []
    for item in value.split(","):
        try:
            actions.append(VisualActionKind(item.strip()))
        except ValueError:
            continue
    return actions


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
