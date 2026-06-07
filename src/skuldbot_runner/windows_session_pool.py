# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Windows interactive session pool lifecycle for high-density graphical runs."""

from __future__ import annotations

import json
import os
import platform
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any


class WindowsSessionPoolError(RuntimeError):
    """Raised when the Windows session pool cannot allocate a run safely."""


@dataclass(frozen=True)
class WindowsSessionIsolation:
    """Isolation guarantees for one Windows robot session."""

    kind: str
    input_isolated: bool
    clipboard_isolated: bool
    profile_ref: str | None = None
    temp_root_ref: str | None = None
    downloads_root_ref: str | None = None


@dataclass(frozen=True)
class WindowsSessionSlot:
    """Configured Windows robot session available for one concurrent run."""

    session_id: str
    robot_user_ref: str
    credential_ref_key: str
    isolation: WindowsSessionIsolation


@dataclass(frozen=True)
class WindowsSessionLease:
    """A Windows session slot assigned to a single run."""

    run_id: str
    slot: WindowsSessionSlot
    environment: dict[str, str]


_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_SESSION_POOL_KEYS = (
    "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_JSON",
    "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL",
)
_DEDICATED_USER_SESSION = "dedicated_user_session"
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


def should_enable_windows_session_pool(
    environment: Mapping[str, str] | None = None,
    platform_system: str | None = None,
) -> bool:
    """Return true only when the broker is explicitly enabled on Windows."""

    env = environment if environment is not None else os.environ
    system = (platform_system or platform.system()).lower()
    if system != "windows":
        return False

    return _read_bool(env.get("SKULDBOT_WINDOWS_SESSION_BROKER_ENABLED"))


def slots_from_environment(
    environment: Mapping[str, str] | None = None,
) -> list[WindowsSessionSlot]:
    """Parse and validate configured Windows session slots."""

    env = environment if environment is not None else os.environ
    if not _read_bool(env.get("SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED")):
        return []

    raw_pool = next(
        (
            env[key].strip()
            for key in _SESSION_POOL_KEYS
            if env.get(key, "").strip()
        ),
        "",
    )
    if not raw_pool:
        return []

    try:
        parsed = json.loads(raw_pool)
    except json.JSONDecodeError:
        return []

    if not isinstance(parsed, list):
        return []

    slots: dict[str, WindowsSessionSlot] = {}
    for item in parsed:
        slot = _slot_from_payload(item)
        if slot is not None:
            slots[slot.session_id] = slot
    return list(slots.values())


class WindowsInteractiveSessionPool:
    """Allocates configured Windows user sessions to graphical runs."""

    def __init__(
        self,
        slots: list[WindowsSessionSlot],
        base_environment: Mapping[str, str] | None = None,
    ) -> None:
        if not slots:
            raise WindowsSessionPoolError("At least one Windows session slot is required.")
        self._available_slots = list(slots)
        self.base_environment = dict(
            base_environment if base_environment is not None else os.environ
        )
        self._leases: dict[str, WindowsSessionLease] = {}

    @property
    def max_sessions(self) -> int:
        return len(self._available_slots) + len(self._leases)

    @property
    def active_count(self) -> int:
        return len(self._leases)

    @property
    def available_count(self) -> int:
        return len(self._available_slots)

    @property
    def has_available_slot(self) -> bool:
        return self.available_count > 0

    @contextmanager
    def acquire(self, run_id: str) -> Iterator[WindowsSessionLease]:
        """Assign one configured Windows session slot to a run."""

        if run_id in self._leases:
            raise WindowsSessionPoolError(f"Run {run_id} already has a Windows session.")
        if not self._available_slots:
            raise WindowsSessionPoolError("No Windows interactive session slots available.")

        slot = self._available_slots.pop(0)
        environment = self._build_environment(slot)
        lease = WindowsSessionLease(run_id=run_id, slot=slot, environment=environment)
        try:
            self._leases[run_id] = lease
            yield lease
        finally:
            self._leases.pop(run_id, None)
            if slot.session_id not in {item.session_id for item in self._available_slots}:
                self._available_slots.append(slot)
            self._available_slots.sort(key=lambda item: item.session_id)

    def stop_all(self) -> None:
        """Release all local slot reservations owned by this pool."""

        leased_slots = [lease.slot for lease in self._leases.values()]
        self._leases.clear()
        existing_ids = {slot.session_id for slot in self._available_slots}
        self._available_slots.extend(
            slot for slot in leased_slots if slot.session_id not in existing_ids
        )
        self._available_slots.sort(key=lambda item: item.session_id)

    def _build_environment(self, slot: WindowsSessionSlot) -> dict[str, str]:
        environment = dict(self.base_environment)
        environment["SKULDBOT_GRAPHICAL_RUNTIME_PLANE"] = "windows_interactive"
        environment["SKULDBOT_WINDOWS_SESSION_ID"] = slot.session_id
        environment["SKULDBOT_WINDOWS_ROBOT_USER_REF"] = slot.robot_user_ref
        environment["SKULDBOT_WINDOWS_SESSION_CREDENTIAL_REF_KEY"] = (
            slot.credential_ref_key
        )
        environment["SKULDBOT_WINDOWS_SESSION_PROFILE_REF"] = (
            slot.isolation.profile_ref or ""
        )
        environment["SKULDBOT_WINDOWS_SESSION_TEMP_ROOT_REF"] = (
            slot.isolation.temp_root_ref or ""
        )
        environment["SKULDBOT_WINDOWS_SESSION_DOWNLOADS_ROOT_REF"] = (
            slot.isolation.downloads_root_ref or ""
        )
        return environment


def _slot_from_payload(payload: Any) -> WindowsSessionSlot | None:
    if not isinstance(payload, dict):
        return None

    if _contains_plaintext_secret(payload):
        return None

    session_id = _read_string(payload.get("sessionId"))
    robot_user_ref = _read_string(payload.get("robotUserRef"))
    credential_ref_key = _read_string(payload.get("credentialRefKey"))
    if not session_id or not robot_user_ref or not credential_ref_key:
        return None

    isolation = _isolation_from_payload(payload.get("isolation"))
    if isolation is None:
        return None

    return WindowsSessionSlot(
        session_id=session_id,
        robot_user_ref=robot_user_ref,
        credential_ref_key=credential_ref_key,
        isolation=isolation,
    )


def _isolation_from_payload(payload: Any) -> WindowsSessionIsolation | None:
    if not isinstance(payload, dict):
        return None

    kind = _read_string(payload.get("kind"))
    if kind.lower() != _DEDICATED_USER_SESSION:
        return None

    input_isolated = bool(payload.get("inputIsolated"))
    clipboard_isolated = bool(payload.get("clipboardIsolated"))
    if not input_isolated or not clipboard_isolated:
        return None

    return WindowsSessionIsolation(
        kind=kind.lower(),
        input_isolated=input_isolated,
        clipboard_isolated=clipboard_isolated,
        profile_ref=_read_string(payload.get("profileRef")),
        temp_root_ref=_read_string(payload.get("tempRootRef")),
        downloads_root_ref=_read_string(payload.get("downloadsRootRef")),
    )


def _contains_plaintext_secret(payload: Mapping[str, Any]) -> bool:
    for key in payload:
        normalized = "".join(ch for ch in key.lower() if ch.isalnum())
        if normalized == "credentialrefkey":
            continue
        if normalized in _PLAINTEXT_SECRET_KEYS:
            return True
    return False


def _read_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _read_bool(value: str | None) -> bool:
    return bool(value and value.strip().lower() in _TRUE_VALUES)
