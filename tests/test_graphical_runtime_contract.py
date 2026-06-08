# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import json

from skuldbot_runner.graphical_runtime import (
    GraphicalProbeInput,
    build_display_lease_environment,
    build_graphical_capabilities,
    read_display_lease_context,
    windows_session_pool_capacity_from_environment,
)
from skuldbot_runner.models import (
    DisplayLease,
    DisplayLeaseState,
    GraphicalDisplayState,
    GraphicalRuntimePlane,
    GraphicalSessionMode,
    HeartbeatRequest,
    RegisterRequest,
    SystemInfo,
    VisualActionKind,
)
from skuldbot_runner.payloads import build_heartbeat_payload


def _windows_session_pool(*session_ids: str) -> str:
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
                },
            }
            for session_id in session_ids
        ]
    )


def _system_info() -> SystemInfo:
    return SystemInfo(
        hostname="runner-01",
        os="Linux",
        os_version="6.0",
        python_version="3.12",
        cpu_count=8,
        memory_total_mb=32768,
        memory_available_mb=16384,
    )


def _display_lease() -> DisplayLease:
    return DisplayLease.model_validate(
        {
            "leaseId": "lease-1",
            "state": "granted",
            "runnerId": "runner-1",
            "grantedAt": "2026-06-03T10:00:00Z",
            "expiresAt": "2026-06-03T10:10:00Z",
            "request": {
                "leaseRequestId": "lease-request-1",
                "tenantId": "tenant-1",
                "runId": "run-1",
                "stepId": "step-1",
                "runtimePlane": "linux_virtual_display",
                "mode": "unattended",
                "requiredCapabilities": ["meditech"],
                "requiredVisualActions": ["screenshot", "ocr_region"],
                "sessionCredentialRefs": [],
                "reason": "visual runtime",
            },
            "session": {
                "sessionId": "session-1",
                "tenantId": "tenant-1",
                "runnerId": "runner-1",
                "runtimePlane": "linux_virtual_display",
                "mode": "unattended",
                "acquiredAt": "2026-06-03T10:00:00Z",
                "display": {
                    "state": "active",
                    "locked": False,
                    "connected": True,
                    "resolution": {"width": 1280, "height": 720},
                    "dpiScale": 1.0,
                    "staleAfterSeconds": 30,
                },
            },
        }
    )


def test_display_lease_environment_round_trip():
    environment = build_display_lease_environment(_display_lease())

    context = read_display_lease_context(environment)

    assert context is not None
    assert context.lease_id == "lease-1"
    assert context.run_id == "run-1"
    assert context.state is DisplayLeaseState.GRANTED
    assert context.runtime_plane is GraphicalRuntimePlane.LINUX_VIRTUAL_DISPLAY
    assert context.mode is GraphicalSessionMode.UNATTENDED
    assert context.allows(VisualActionKind.SCREENSHOT)
    assert context.allows(VisualActionKind.OCR_REGION)


def test_no_display_signal_declares_no_graphical_capability():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Linux",
            environment={"SKULDBOT_CAPABILITIES": "desktop"},
        )
    )

    assert capability is None


def test_legacy_has_display_signal_is_ignored():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Linux",
            environment={"HASDISPLAY": "true"},
        )
    )

    assert capability is None


def test_linux_display_declares_linux_graphical_capability():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Linux",
            environment={
                "DISPLAY": ":99",
                "SKULDBOT_GRAPHICAL_UNATTENDED": "true",
                "SKULDBOT_DISPLAY_WIDTH": "1920",
                "SKULDBOT_DISPLAY_HEIGHT": "1080",
                "SKULDBOT_INSTALLED_SYSTEMS": "meditech,sap",
            },
        )
    )

    assert capability is not None
    assert capability.has_display is True
    assert capability.display.state is GraphicalDisplayState.AVAILABLE
    assert capability.display.resolution.width == 1920
    assert capability.display.resolution.height == 1080
    assert capability.supported_runtime_planes == [
        GraphicalRuntimePlane.LINUX_VIRTUAL_DISPLAY
    ]
    assert capability.supported_session_modes == [GraphicalSessionMode.UNATTENDED]


def test_linux_virtual_display_pool_declares_multi_session_capacity_without_global_display():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Linux",
            environment={
                "SKULDBOT_LINUX_VIRTUAL_DISPLAY_ENABLED": "true",
                "SKULDBOT_GRAPHICAL_UNATTENDED": "true",
            },
            max_graphical_sessions=3,
            current_graphical_sessions=2,
        )
    )

    assert capability is not None
    assert capability.supported_runtime_planes == [
        GraphicalRuntimePlane.LINUX_VIRTUAL_DISPLAY
    ]
    assert capability.supported_session_modes == [GraphicalSessionMode.UNATTENDED]
    assert capability.max_graphical_sessions == 3
    assert capability.current_graphical_sessions == 2


def test_linux_shared_display_does_not_declare_multi_session_without_pool():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Linux",
            environment={
                "DISPLAY": ":1",
                "SKULDBOT_MAX_GRAPHICAL_SESSIONS": "3",
            },
        )
    )

    assert capability is not None
    assert capability.max_graphical_sessions == 1


def test_windows_interactive_does_not_declare_multi_session_without_session_pool():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment={
                "SESSIONNAME": "console",
                "SKULDBOT_MAX_GRAPHICAL_SESSIONS": "3",
            },
        )
    )

    assert capability is not None
    assert capability.supported_runtime_planes == [
        GraphicalRuntimePlane.WINDOWS_INTERACTIVE
    ]
    assert capability.supported_session_modes == [GraphicalSessionMode.ATTENDED]
    assert capability.max_graphical_sessions == 1


def test_windows_interactive_session_pool_can_declare_multi_session_capacity():
    environment = {
        "SESSIONNAME": "console",
        "SKULDBOT_MAX_GRAPHICAL_SESSIONS": "3",
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED": "true",
        "SKULDBOT_WINDOWS_SESSION_BROKER_ENABLED": "true",
        "SKULDBOT_WINDOWS_SESSION_BROKER_COMMAND": "skuldbot-win-broker",
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_JSON": (
            _windows_session_pool("session-1", "session-2")
        ),
    }
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment=environment,
            windows_session_pool_capacity=(
                windows_session_pool_capacity_from_environment(environment)
            ),
        )
    )

    assert capability is not None
    assert capability.supported_runtime_planes == [
        GraphicalRuntimePlane.WINDOWS_INTERACTIVE
    ]
    assert capability.supported_session_modes == [GraphicalSessionMode.UNATTENDED]
    assert capability.supported_session_modes == [GraphicalSessionMode.UNATTENDED]
    assert capability.max_graphical_sessions == 2
    assert VisualActionKind.IMAGE_CLICK in capability.supported_visual_actions


def test_windows_session_pool_without_broker_does_not_declare_multi_session():
    environment = {
        "SESSIONNAME": "console",
        "SKULDBOT_MAX_GRAPHICAL_SESSIONS": "3",
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED": "true",
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_JSON": (
            _windows_session_pool("session-1", "session-2")
        ),
    }
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment=environment,
            windows_session_pool_capacity=(
                windows_session_pool_capacity_from_environment(environment)
            ),
        )
    )

    assert windows_session_pool_capacity_from_environment(environment) == 0
    assert capability is not None
    assert capability.supported_session_modes == [GraphicalSessionMode.ATTENDED]
    assert capability.max_graphical_sessions == 1


def test_windows_session_pool_flag_without_slots_does_not_declare_multi_session():
    environment = {
        "SESSIONNAME": "console",
        "SKULDBOT_MAX_GRAPHICAL_SESSIONS": "3",
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED": "true",
        "SKULDBOT_WINDOWS_SESSION_BROKER_ENABLED": "true",
        "SKULDBOT_WINDOWS_SESSION_BROKER_COMMAND": "skuldbot-win-broker",
    }
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment=environment,
            windows_session_pool_capacity=(
                windows_session_pool_capacity_from_environment(environment)
            ),
        )
    )

    assert capability is not None
    assert capability.supported_session_modes == [GraphicalSessionMode.ATTENDED]
    assert capability.max_graphical_sessions == 1


def test_windows_session_pool_rejects_malformed_pool_config():
    environment = {
        "SESSIONNAME": "console",
        "SKULDBOT_MAX_GRAPHICAL_SESSIONS": "3",
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED": "true",
        "SKULDBOT_WINDOWS_SESSION_BROKER_ENABLED": "true",
        "SKULDBOT_WINDOWS_SESSION_BROKER_COMMAND": "skuldbot-win-broker",
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_JSON": "not-json",
    }
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment=environment,
            windows_session_pool_capacity=(
                windows_session_pool_capacity_from_environment(environment)
            ),
        )
    )

    assert windows_session_pool_capacity_from_environment(environment) == 0
    assert capability is not None
    assert capability.supported_session_modes == [GraphicalSessionMode.ATTENDED]
    assert capability.max_graphical_sessions == 1


def test_windows_session_pool_rejects_plaintext_credentials():
    environment = {
        "SESSIONNAME": "console",
        "SKULDBOT_MAX_GRAPHICAL_SESSIONS": "3",
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED": "true",
        "SKULDBOT_WINDOWS_SESSION_BROKER_ENABLED": "true",
        "SKULDBOT_WINDOWS_SESSION_BROKER_COMMAND": "skuldbot-win-broker",
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_JSON": json.dumps(
            [
                {
                    "sessionId": "session-1",
                    "robotUserRef": "robot-user-ref-session-1",
                    "credentialRefKey": "vault-key-session-1",
                    "password": "not-allowed",
                    "isolation": {
                        "kind": "dedicated_user_session",
                        "inputIsolated": True,
                        "clipboardIsolated": True,
                    },
                }
            ]
        ),
    }
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment=environment,
            windows_session_pool_capacity=(
                windows_session_pool_capacity_from_environment(environment)
            ),
        )
    )

    assert windows_session_pool_capacity_from_environment(environment) == 0
    assert capability is not None
    assert capability.supported_session_modes == [GraphicalSessionMode.ATTENDED]
    assert capability.max_graphical_sessions == 1


def test_windows_session_pool_requires_dedicated_input_and_clipboard_isolation():
    environment = {
        "SESSIONNAME": "console",
        "SKULDBOT_MAX_GRAPHICAL_SESSIONS": "3",
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED": "true",
        "SKULDBOT_WINDOWS_SESSION_BROKER_ENABLED": "true",
        "SKULDBOT_WINDOWS_SESSION_BROKER_COMMAND": "skuldbot-win-broker",
        "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_JSON": json.dumps(
            [
                {
                    "sessionId": "session-1",
                    "robotUserRef": "robot-user-ref-session-1",
                    "credentialRefKey": "vault-key-session-1",
                    "isolation": {
                        "kind": "shared_desktop",
                        "inputIsolated": True,
                        "clipboardIsolated": True,
                    },
                },
                {
                    "sessionId": "session-2",
                    "robotUserRef": "robot-user-ref-session-2",
                    "credentialRefKey": "vault-key-session-2",
                    "isolation": {
                        "kind": "dedicated_user_session",
                        "inputIsolated": True,
                        "clipboardIsolated": False,
                    },
                },
            ]
        ),
    }
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment=environment,
            windows_session_pool_capacity=(
                windows_session_pool_capacity_from_environment(environment)
            ),
        )
    )

    assert windows_session_pool_capacity_from_environment(environment) == 0
    assert capability is not None
    assert capability.supported_session_modes == [GraphicalSessionMode.ATTENDED]
    assert capability.max_graphical_sessions == 1


def test_windows_services_session_declares_no_graphical_capability():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment={"SESSIONNAME": "Services"},
        )
    )

    assert capability is None


def test_windows_interactive_session_declares_attended_only():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment={"SESSIONNAME": "Console"},
        )
    )

    assert capability is not None
    assert capability.supported_runtime_planes == [
        GraphicalRuntimePlane.WINDOWS_INTERACTIVE
    ]
    assert capability.supported_session_modes == [GraphicalSessionMode.ATTENDED]


def test_windows_interactive_attached_worker_declares_display():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment={
                "SKULDBOT_GRAPHICAL_RUNTIME_PLANE": "windows_interactive",
                "SKULDBOT_WINDOWS_SESSION_ATTACHED": "1",
            },
        )
    )

    assert capability is not None
    assert capability.supported_runtime_planes == [
        GraphicalRuntimePlane.WINDOWS_INTERACTIVE
    ]


def test_locked_display_is_reported_but_not_route_ready():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Linux",
            environment={"DISPLAY": ":1", "SKULDBOT_DISPLAY_LOCKED": "true"},
        )
    )

    assert capability is not None
    assert capability.has_display is False
    assert capability.display.locked is True
    assert capability.display.state is GraphicalDisplayState.LOCKED


def test_citrix_plane_requires_explicit_citrix_session_and_display():
    without_citrix = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment={
                "SESSIONNAME": "Console",
                "SKULDBOT_GRAPHICAL_RUNTIME_PLANE": "citrix_published_app",
            },
        )
    )
    with_citrix = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Windows",
            environment={
                "SESSIONNAME": "Console",
                "SKULDBOT_GRAPHICAL_RUNTIME_PLANE": "citrix_published_app",
                "SKULDBOT_CITRIX_SESSION": "true",
            },
        )
    )

    assert without_citrix is None
    assert with_citrix is not None
    assert with_citrix.supported_runtime_planes == [
        GraphicalRuntimePlane.CITRIX_PUBLISHED_APP
    ]


def test_register_payload_uses_orchestrator_graphical_contract_names():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Linux",
            environment={"DISPLAY": ":99"},
        )
    )
    request = RegisterRequest(
        name="runner-01",
        labels={},
        capabilities=["desktop"],
        system_info=_system_info(),
        graphical_capabilities=capability,
    )

    payload = request.model_dump(by_alias=True, exclude_none=True)

    assert "systemInfo" in payload
    assert "system_info" not in payload
    assert payload["graphicalCapabilities"]["hasDisplay"] is True
    assert payload["graphicalCapabilities"]["display"]["dpiScale"] == 1.0
    assert payload["graphicalCapabilities"]["supportedVisualActions"] == [
        "screenshot",
        "wait_image",
        "image_click",
        "ocr_region",
        "assert_text",
        "type_text",
        "hotkey",
    ]


def test_heartbeat_payload_can_carry_graphical_contract_names():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Linux",
            environment={"DISPLAY": ":99"},
        )
    )
    request = HeartbeatRequest(
        status="online",
        system_info=_system_info(),
        graphical_capabilities=capability,
    )

    payload = request.graphical_capabilities.model_dump(
        by_alias=True,
        exclude_none=True,
    )

    assert payload["hasDisplay"] is True
    assert payload["display"]["staleAfterSeconds"] == 30
    assert payload["supportedRuntimePlanes"] == ["linux_virtual_display"]


def test_heartbeat_contract_payload_includes_graphical_capability_when_declared():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Linux",
            environment={"DISPLAY": ":99"},
        )
    )
    request = HeartbeatRequest(
        status="busy",
        current_run_id="run-123",
        system_info=_system_info(),
        graphical_capabilities=capability,
    )

    payload = build_heartbeat_payload(request)

    assert payload["status"] == "busy"
    assert payload["currentRunId"] == "run-123"
    assert payload["metrics"] == {
        "cpuPercent": 0,
        "memoryPercent": 0,
        "activeSteps": 0,
    }
    assert payload["graphicalCapabilities"]["hasDisplay"] is True
    assert payload["graphicalCapabilities"]["supportedRuntimePlanes"] == [
        "linux_virtual_display"
    ]
