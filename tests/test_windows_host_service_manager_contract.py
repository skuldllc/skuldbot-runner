# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import argparse
from pathlib import Path

from skuldbot_runner.windows_host_service_manager import (
    WindowsHostServiceManagerError,
    _check_pipe_health,
    _current_service_state,
    _run_pywin32_service_command,
    build_failure_actions_config,
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


def test_windows_host_service_manager_supports_direct_pywin32_import_mode():
    source = Path("src/skuldbot_runner/windows_host_service_manager.py").read_text()

    assert "except ImportError" in source
    assert "from skuldbot_runner.windows_host_service import" in source
    assert "from skuldbot_runner.windows_native_launcher import" in source


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


def test_windows_host_service_manager_accepts_health_action():
    config = parse_manager_config(_args(action="health"))

    assert config.action == "health"


def test_windows_host_service_manager_rejects_custom_pipe_until_profiled():
    config = parse_manager_config(_args(pipe=r"\\.\pipe\custom-skuld"))

    try:
        build_service_module_arguments(config)
        raise AssertionError("custom pipes should wait for orchestrator-managed profiles")
    except WindowsHostServiceManagerError as exc:
        assert "orchestrator-managed profile" in str(exc)


def test_windows_host_service_manager_places_pywin32_options_before_install(monkeypatch):
    captured = {}
    service_config = {}

    def _fake_handle_command_line(_service_class):
        import sys

        captured["argv"] = list(sys.argv)

    class FakeWin32Service:
        SC_ACTION_RESTART = 1
        SC_MANAGER_CONNECT = 2
        SERVICE_CHANGE_CONFIG = 3
        SERVICE_CONFIG_FAILURE_ACTIONS = 4

        @staticmethod
        def OpenSCManager(_machine, _database, access):
            service_config["scm_access"] = access
            return "scm-handle"

        @staticmethod
        def OpenService(scm_handle, service_name, access):
            service_config["open"] = (scm_handle, service_name, access)
            return "service-handle"

        @staticmethod
        def ChangeServiceConfig2(service_handle, info_level, config):
            service_config["failure_actions"] = (service_handle, info_level, config)

        @staticmethod
        def CloseServiceHandle(handle):
            service_config.setdefault("closed", []).append(handle)

    monkeypatch.setattr(
        "skuldbot_runner.windows_host_service_manager.win32serviceutil",
        type(
            "Win32ServiceUtil",
            (),
            {"HandleCommandLine": staticmethod(_fake_handle_command_line)},
        ),
    )
    monkeypatch.setattr(
        "skuldbot_runner.windows_host_service_manager.win32service",
        FakeWin32Service,
    )
    monkeypatch.setattr(
        "skuldbot_runner.windows_host_service_manager.SkuldBotWindowsHostService",
        object,
    )

    result = _run_pywin32_service_command(parse_manager_config(_args(action="install")))

    assert result == 0
    assert captured["argv"][1:] == [
        "--startup",
        "auto",
        "install",
    ]
    assert service_config["scm_access"] == FakeWin32Service.SC_MANAGER_CONNECT
    assert service_config["open"] == (
        "scm-handle",
        "SkuldBotWindowsHostService",
        FakeWin32Service.SERVICE_CHANGE_CONFIG,
    )
    assert service_config["failure_actions"][0:2] == (
        "service-handle",
        FakeWin32Service.SERVICE_CONFIG_FAILURE_ACTIONS,
    )
    assert service_config["failure_actions"][2] == {
        "ResetPeriod": 86400,
        "RebootMsg": "",
        "Command": "",
        "Actions": [(1, 60000), (1, 60000), (1, 60000)],
    }
    assert service_config["closed"] == ["service-handle", "scm-handle"]


def test_windows_host_service_manager_builds_restart_failure_policy():
    fake_win32service = type("FakeWin32Service", (), {"SC_ACTION_RESTART": 7})

    assert build_failure_actions_config(fake_win32service) == {
        "ResetPeriod": 86400,
        "RebootMsg": "",
        "Command": "",
        "Actions": [(7, 60000), (7, 60000), (7, 60000)],
    }


def test_windows_host_service_manager_does_not_pass_install_options_to_start(monkeypatch):
    captured = {}

    def _fake_handle_command_line(_service_class):
        import sys

        captured["argv"] = list(sys.argv)

    monkeypatch.setattr(
        "skuldbot_runner.windows_host_service_manager.win32serviceutil",
        type(
            "Win32ServiceUtil",
            (),
            {"HandleCommandLine": staticmethod(_fake_handle_command_line)},
        ),
    )
    monkeypatch.setattr(
        "skuldbot_runner.windows_host_service_manager.SkuldBotWindowsHostService",
        object,
    )

    result = _run_pywin32_service_command(parse_manager_config(_args(action="start")))

    assert result == 0
    assert captured["argv"][1:] == ["start"]


def test_windows_host_service_manager_remove_stops_service_before_delete(monkeypatch):
    captured = {"states": [4, 3, 1]}

    def _fake_handle_command_line(_service_class):
        import sys

        captured["argv"] = list(sys.argv)

    class FakeWin32Service:
        SERVICE_STOPPED = 1

    class FakeWin32ServiceUtil:
        @staticmethod
        def HandleCommandLine(_service_class):
            _fake_handle_command_line(_service_class)

        @staticmethod
        def QueryServiceStatus(_service_name):
            state = captured["states"].pop(0)
            captured.setdefault("queried", []).append(state)
            return (16, state)

        @staticmethod
        def StopService(service_name):
            captured["stopped"] = service_name

    monkeypatch.setattr(
        "skuldbot_runner.windows_host_service_manager.win32serviceutil",
        FakeWin32ServiceUtil,
    )
    monkeypatch.setattr(
        "skuldbot_runner.windows_host_service_manager.win32service",
        FakeWin32Service,
    )
    monkeypatch.setattr("skuldbot_runner.windows_host_service_manager.time.sleep", lambda _s: None)
    monkeypatch.setattr(
        "skuldbot_runner.windows_host_service_manager.SkuldBotWindowsHostService",
        object,
    )

    result = _run_pywin32_service_command(parse_manager_config(_args(action="remove")))

    assert result == 0
    assert captured["stopped"] == "SkuldBotWindowsHostService"
    assert captured["queried"] == [4, 3, 1]
    assert captured["argv"][1:] == ["remove"]


def test_windows_host_service_manager_health_action_checks_scm_and_pipe(monkeypatch, capsys):
    captured = {}

    class FakeWin32Service:
        SERVICE_RUNNING = 4

    class FakeWin32ServiceUtil:
        @staticmethod
        def QueryServiceStatus(service_name):
            captured["queried"] = service_name
            return (16, 4)

    monkeypatch.setattr(
        "skuldbot_runner.windows_host_service_manager.win32serviceutil",
        FakeWin32ServiceUtil,
    )
    monkeypatch.setattr(
        "skuldbot_runner.windows_host_service_manager.win32service",
        FakeWin32Service,
    )
    monkeypatch.setattr(
        "skuldbot_runner.windows_host_service_manager._check_pipe_health",
        lambda pipe_name: {"accepted": True, "status": "healthy"},
    )

    result = _run_pywin32_service_command(parse_manager_config(_args(action="health")))

    assert result == 0
    assert captured["queried"] == "SkuldBotWindowsHostService"
    assert "healthy" in capsys.readouterr().out


def test_windows_host_service_manager_pipe_health_uses_real_named_pipe_protocol(monkeypatch):
    captured = {}

    class FakeWin32Con:
        GENERIC_READ = 1
        GENERIC_WRITE = 2
        OPEN_EXISTING = 3

    class FakeWin32Pipe:
        PIPE_READMODE_MESSAGE = 4

        @staticmethod
        def WaitNamedPipe(pipe_name, timeout_ms):
            captured["wait"] = (pipe_name, timeout_ms)

        @staticmethod
        def SetNamedPipeHandleState(pipe, mode, _max_collection_count, _collect_data_timeout):
            captured["mode"] = (pipe, mode)

    class FakeWin32File:
        @staticmethod
        def CreateFile(pipe_name, access, share_mode, security_attrs, creation, flags, template):
            captured["create"] = (
                pipe_name,
                access,
                share_mode,
                security_attrs,
                creation,
                flags,
                template,
            )
            return "pipe-handle"

        @staticmethod
        def WriteFile(pipe, data):
            captured["write"] = (pipe, data)

        @staticmethod
        def ReadFile(pipe, size):
            captured["read"] = (pipe, size)
            return 0, b'{"accepted":true,"status":"healthy"}'

        @staticmethod
        def CloseHandle(pipe):
            captured["closed"] = pipe

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "win32con":
            return FakeWin32Con
        if name == "win32pipe":
            return FakeWin32Pipe
        if name == "win32file":
            return FakeWin32File
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)

    response = _check_pipe_health(r"\\.\pipe\skuldbot-windows-session-launcher")

    assert response == {"accepted": True, "status": "healthy"}
    assert captured["wait"] == (r"\\.\pipe\skuldbot-windows-session-launcher", 5000)
    assert captured["create"][0] == r"\\.\pipe\skuldbot-windows-session-launcher"
    assert captured["write"] == (
        "pipe-handle",
        b'{"protocolVersion": 1, "action": "health"}',
    )
    assert captured["read"] == ("pipe-handle", 65536)
    assert captured["closed"] == "pipe-handle"


def test_windows_host_service_manager_reads_service_state_from_pywin32_tuple():
    assert _current_service_state((16, 4, 0, 0)) == 4


def test_windows_host_service_manager_fails_closed_on_non_windows_host():
    assert main(["status"]) == 1
