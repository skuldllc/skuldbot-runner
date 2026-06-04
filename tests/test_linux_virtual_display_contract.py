# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import os
import shutil

import pytest

from skuldbot_runner.graphical_runtime import (
    GraphicalProbeInput,
    build_graphical_capabilities,
)
from skuldbot_runner.linux_virtual_display import (
    LinuxVirtualDisplayConfig,
    LinuxVirtualDisplaySession,
    build_xvfb_command,
    config_from_environment,
    display_for_slot,
    should_start_linux_virtual_display,
)
from skuldbot_runner.models import GraphicalRuntimePlane, GraphicalSessionMode


def test_linux_virtual_display_starts_only_when_explicitly_requested():
    assert (
        should_start_linux_virtual_display(
            {"SKULDBOT_LINUX_VIRTUAL_DISPLAY_ENABLED": "true"},
            platform_system="Linux",
        )
        is True
    )
    assert (
        should_start_linux_virtual_display(
            {"SKULDBOT_LINUX_VIRTUAL_DISPLAY_ENABLED": "true", "DISPLAY": ":1"},
            platform_system="Linux",
        )
        is False
    )
    assert (
        should_start_linux_virtual_display(
            {"SKULDBOT_LINUX_VIRTUAL_DISPLAY_ENABLED": "true"},
            platform_system="Darwin",
        )
        is False
    )


def test_xvfb_command_is_deterministic_and_local_only():
    command = build_xvfb_command(
        LinuxVirtualDisplayConfig(display=":91", width=1600, height=900, depth=24)
    )

    assert command == [
        "Xvfb",
        ":91",
        "-screen",
        "0",
        "1600x900x24",
        "-nolisten",
        "tcp",
    ]


def test_linux_virtual_display_config_reads_environment():
    config = config_from_environment(
        {
            "SKULDBOT_LINUX_VIRTUAL_DISPLAY": ":92",
            "SKULDBOT_DISPLAY_WIDTH": "1920",
            "SKULDBOT_DISPLAY_HEIGHT": "1080",
            "SKULDBOT_DISPLAY_DEPTH": "24",
        }
    )

    assert config.display == ":92"
    assert config.screen_geometry == "1920x1080x24"


def test_linux_virtual_display_slots_are_isolated_and_deterministic():
    assert display_for_slot(":100", 0) == ":100"
    assert display_for_slot(":100", 1) == ":101"
    assert display_for_slot(":100", 2) == ":102"


@pytest.mark.skipif(
    os.environ.get("SKULDBOT_XVFB_INTEGRATION") != "1",
    reason="Set SKULDBOT_XVFB_INTEGRATION=1 to start a real Xvfb process.",
)
def test_linux_virtual_display_starts_real_xvfb_and_declares_capability():
    if shutil.which("Xvfb") is None:
        pytest.skip("Xvfb executable is not available.")

    environment: dict[str, str] = {}
    session = LinuxVirtualDisplaySession(
        LinuxVirtualDisplayConfig(display=":94", startup_timeout_seconds=2.0),
        environment=environment,
    )

    try:
        session.start()
        capability = build_graphical_capabilities(
            GraphicalProbeInput(
                platform_system="Linux",
                environment=environment,
            )
        )

        assert session.is_running
        assert environment["DISPLAY"] == ":94"
        assert capability is not None
        assert capability.supported_runtime_planes == [
            GraphicalRuntimePlane.LINUX_VIRTUAL_DISPLAY
        ]
        assert capability.supported_session_modes == [GraphicalSessionMode.UNATTENDED]
    finally:
        session.stop()
