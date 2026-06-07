# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import json

from skuldbot_runner.windows_session_pool import (
    WindowsInteractiveSessionPool,
    WindowsSessionPoolError,
    should_enable_windows_session_pool,
    slots_from_environment,
)


def _pool_payload(*session_ids: str, extra: dict | None = None) -> str:
    return json.dumps(
        [
            {
                "sessionId": session_id,
                "robotUserRef": f"robot-user-ref-{session_id}",
                "credentialRefKey": f"vault-key-{session_id}",
                "isolation": {
                    "kind": "dedicated_user_session",
                    "inputIsolated": True,
                    "clipboardIsolated": True,
                    "profileRef": f"profile://{session_id}",
                    "tempRootRef": f"temp://{session_id}",
                    "downloadsRootRef": f"downloads://{session_id}",
                },
                **(extra or {}),
            }
            for session_id in session_ids
        ]
    )


def _slots(*session_ids: str):
    return slots_from_environment(
        {
            "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED": "true",
            "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_JSON": _pool_payload(
                *session_ids
            ),
        }
    )


def test_windows_session_pool_requires_windows_broker_opt_in():
    assert (
        should_enable_windows_session_pool(
            environment={"SKULDBOT_WINDOWS_SESSION_BROKER_ENABLED": "true"},
            platform_system="Linux",
        )
        is False
    )
    assert (
        should_enable_windows_session_pool(
            environment={"SKULDBOT_WINDOWS_SESSION_BROKER_ENABLED": "true"},
            platform_system="Windows",
        )
        is True
    )


def test_windows_session_pool_parses_only_valid_dedicated_slots():
    slots = slots_from_environment(
        {
            "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED": "true",
            "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_JSON": json.dumps(
                [
                    {
                        "sessionId": "valid-1",
                        "robotUserRef": "robot-user-ref-valid-1",
                        "credentialRefKey": "vault-key-valid-1",
                        "isolation": {
                            "kind": "dedicated_user_session",
                            "inputIsolated": True,
                            "clipboardIsolated": True,
                        },
                    },
                    {
                        "sessionId": "invalid-shared",
                        "robotUserRef": "robot-user-ref-invalid",
                        "credentialRefKey": "vault-key-invalid",
                        "isolation": {
                            "kind": "shared_desktop",
                            "inputIsolated": True,
                            "clipboardIsolated": True,
                        },
                    },
                    {
                        "sessionId": "invalid-plaintext",
                        "robotUserRef": "robot-user-ref-invalid",
                        "credentialRefKey": "vault-key-invalid",
                        "password": "not-allowed",
                        "isolation": {
                            "kind": "dedicated_user_session",
                            "inputIsolated": True,
                            "clipboardIsolated": True,
                        },
                    },
                ]
            ),
        }
    )

    assert [slot.session_id for slot in slots] == ["valid-1"]


def test_windows_session_pool_allocates_distinct_slots_and_reuses_after_release():
    pool = WindowsInteractiveSessionPool(
        slots=_slots("session-1", "session-2"),
        base_environment={},
    )

    assert pool.max_sessions == 2
    assert pool.active_count == 0
    assert pool.available_count == 2

    with pool.acquire("run-a") as lease_a:
        assert lease_a.slot.session_id == "session-1"
        assert lease_a.environment["SKULDBOT_WINDOWS_SESSION_ID"] == "session-1"
        assert lease_a.environment["SKULDBOT_WINDOWS_SESSION_CREDENTIAL_REF_KEY"] == (
            "vault-key-session-1"
        )
        assert pool.active_count == 1
        assert pool.available_count == 1

        with pool.acquire("run-b") as lease_b:
            assert lease_b.slot.session_id == "session-2"
            assert lease_b.environment["SKULDBOT_WINDOWS_SESSION_ID"] == "session-2"
            assert pool.active_count == 2
            assert pool.available_count == 0

    assert pool.active_count == 0
    assert pool.available_count == 2

    with pool.acquire("run-c") as lease_c:
        assert lease_c.slot.session_id == "session-1"


def test_windows_session_pool_rejects_exhaustion_and_duplicate_run():
    pool = WindowsInteractiveSessionPool(slots=_slots("session-1"), base_environment={})

    try:
        with pool.acquire("run-a"):
            try:
                with pool.acquire("run-a"):
                    raise AssertionError("duplicate run should not acquire a slot")
            except WindowsSessionPoolError as exc:
                assert "already has a Windows session" in str(exc)

            try:
                with pool.acquire("run-b"):
                    raise AssertionError("exhausted pool should not acquire a slot")
            except WindowsSessionPoolError as exc:
                assert "No Windows interactive session slots available" in str(exc)
    finally:
        pool.stop_all()

    assert pool.active_count == 0
    assert pool.available_count == 1


def test_windows_session_pool_stop_all_releases_slots():
    pool = WindowsInteractiveSessionPool(
        slots=_slots("session-1", "session-2"),
        base_environment={},
    )
    context = pool.acquire("run-a")
    context.__enter__()

    assert pool.active_count == 1
    pool.stop_all()

    assert pool.active_count == 0
    assert pool.available_count == 2
    context.__exit__(None, None, None)
