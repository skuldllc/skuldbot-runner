# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import argparse

from skuldbot_runner.windows_host_service_manager import (
    WindowsHostServiceManagerError,
    build_service_module_arguments,
    main,
    parse_manager_config,
)


def _args(**overrides):
    values = {
        "action": "install",
        "pipe": r"\\.\pipe\skuldbot-windows-session-launcher",
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_windows_host_service_manager_config_is_refs_only():
    config = parse_manager_config(_args())

    assert config.action == "install"
    assert config.pipe_name == r"\\.\pipe\skuldbot-windows-session-launcher"
    service_args = build_service_module_arguments(config)
    serialized = " ".join(service_args).lower()
    assert service_args == []
    assert "password" not in serialized
    assert "secret" not in serialized
    assert "token" not in serialized


def test_windows_host_service_manager_rejects_secret_like_pipe_name():
    for pipe in (
        r"\\.\pipe\skuldbot-password",
        r"\\.\pipe\skuldbot-secret",
        r"\\.\pipe\skuldbot-token",
        "",
    ):
        try:
            parse_manager_config(_args(pipe=pipe))
            raise AssertionError(f"manager should reject pipe={pipe!r}")
        except WindowsHostServiceManagerError:
            pass


def test_windows_host_service_manager_rejects_unknown_action():
    try:
        parse_manager_config(_args(action="run-arbitrary-command"))
        raise AssertionError("manager should reject unsupported action")
    except WindowsHostServiceManagerError:
        pass


def test_windows_host_service_manager_rejects_custom_pipe_until_profiled():
    config = parse_manager_config(_args(pipe=r"\\.\pipe\custom-skuld"))

    try:
        build_service_module_arguments(config)
        raise AssertionError("custom pipes should wait for orchestrator-managed profiles")
    except WindowsHostServiceManagerError as exc:
        assert "orchestrator-managed profile" in str(exc)


def test_windows_host_service_manager_fails_closed_on_non_windows_host():
    assert main(["status"]) == 1
