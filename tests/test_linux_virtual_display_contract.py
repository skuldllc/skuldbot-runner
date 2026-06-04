# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import os
import shutil
import subprocess
from contextlib import ExitStack

import pytest

from skuldbot_runner.graphical_runtime import (
    GraphicalProbeInput,
    build_graphical_capabilities,
)
from skuldbot_runner.linux_virtual_display import (
    LinuxVirtualDisplayConfig,
    LinuxVirtualDisplayError,
    LinuxVirtualDisplayLease,
    LinuxVirtualDisplayPool,
    LinuxVirtualDisplaySession,
    build_xvfb_command,
    config_from_environment,
    display_for_slot,
    should_start_linux_virtual_display,
)
from skuldbot_runner.models import GraphicalRuntimePlane, GraphicalSessionMode


def _assert_x_display_ready(lease: LinuxVirtualDisplayLease) -> None:
    if shutil.which("xdpyinfo") is None:
        return

    probe_env = dict(os.environ)
    probe_env.update(lease.environment)
    result = subprocess.run(  # noqa: S603
        ["xdpyinfo"],
        env=probe_env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    assert result.returncode == 0


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


def test_linux_virtual_display_respects_explicit_empty_environments(monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("SKULDBOT_LINUX_VIRTUAL_DISPLAY_ENABLED", "true")

    assert (
        should_start_linux_virtual_display(
            {},
            platform_system="Linux",
        )
        is False
    )

    monkeypatch.setenv("DISPLAY", ":55")
    monkeypatch.setenv("SKULDBOT_LINUX_VIRTUAL_DISPLAY", ":56")
    monkeypatch.setenv("SKULDBOT_DISPLAY_WIDTH", "1600")
    config = config_from_environment({})
    session_environment: dict[str, str] = {}
    session = LinuxVirtualDisplaySession(config, environment=session_environment)
    pool = LinuxVirtualDisplayPool(
        base_config=config,
        max_sessions=1,
        base_environment={},
    )

    assert config.display == ":99"
    assert config.width == 1280
    assert session.environment is session_environment
    assert pool.base_environment == {}


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


@pytest.mark.skipif(
    os.environ.get("SKULDBOT_XVFB_INTEGRATION") != "1",
    reason="Set SKULDBOT_XVFB_INTEGRATION=1 to start real isolated Xvfb sessions.",
)
def test_linux_virtual_display_pool_runs_isolated_concurrent_sessions():
    if shutil.which("Xvfb") is None:
        pytest.skip("Xvfb executable is not available.")

    pool = LinuxVirtualDisplayPool(
        base_config=LinuxVirtualDisplayConfig(
            display=os.environ.get("SKULDBOT_XVFB_POOL_TEST_DISPLAY", ":100"),
            startup_timeout_seconds=3.0,
        ),
        max_sessions=2,
        base_environment={},
    )

    with ExitStack() as stack:
        lease_a = stack.enter_context(pool.acquire("run-a"))
        lease_b = stack.enter_context(pool.acquire("run-b"))
        first_display = lease_a.display

        assert lease_a.display != lease_b.display
        assert lease_a.environment["DISPLAY"] == lease_a.display
        assert lease_b.environment["DISPLAY"] == lease_b.display
        assert pool.active_count == 2
        assert pool.available_count == 0
        _assert_x_display_ready(lease_a)
        _assert_x_display_ready(lease_b)

        with pytest.raises(
            LinuxVirtualDisplayError,
            match="No Linux virtual display slots are available",
        ):
            with pool.acquire("run-c"):
                pass

        with pytest.raises(
            LinuxVirtualDisplayError,
            match="Run run-a already has a display lease",
        ):
            with pool.acquire("run-a"):
                pass

    assert pool.active_count == 0
    assert pool.available_count == 2

    with pool.acquire("run-c") as lease_c:
        assert lease_c.display == first_display
        _assert_x_display_ready(lease_c)

    assert pool.active_count == 0
    assert pool.available_count == 2


@pytest.mark.skipif(
    os.environ.get("SKULDBOT_XVFB_INTEGRATION") != "1",
    reason="Set SKULDBOT_XVFB_INTEGRATION=1 to start real isolated Xvfb sessions.",
)
def test_linux_virtual_display_pool_stop_all_releases_active_sessions_once():
    if shutil.which("Xvfb") is None:
        pytest.skip("Xvfb executable is not available.")

    pool = LinuxVirtualDisplayPool(
        base_config=LinuxVirtualDisplayConfig(
            display=os.environ.get("SKULDBOT_XVFB_POOL_STOP_TEST_DISPLAY", ":110"),
            startup_timeout_seconds=3.0,
        ),
        max_sessions=2,
        base_environment={},
    )
    lease_a_context = pool.acquire("run-a")
    lease_b_context = pool.acquire("run-b")

    lease_a = lease_a_context.__enter__()
    lease_b = lease_b_context.__enter__()
    try:
        assert lease_a.display != lease_b.display
        assert pool.active_count == 2
        assert pool.available_count == 0

        pool.stop_all()

        assert pool.active_count == 0
        assert pool.available_count == 2
    finally:
        lease_b_context.__exit__(None, None, None)
        lease_a_context.__exit__(None, None, None)

    assert pool.active_count == 0
    assert pool.available_count == 2
