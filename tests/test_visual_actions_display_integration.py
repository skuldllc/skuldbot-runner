# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import json
import os
import shutil
import subprocess
import sys
from contextlib import ExitStack, contextmanager

import pytest

from skuldbot_runner.graphical_runtime import build_display_lease_environment
from skuldbot_runner.linux_virtual_display import (
    LinuxVirtualDisplayConfig,
    LinuxVirtualDisplayPool,
    LinuxVirtualDisplaySession,
)
from skuldbot_runner.models import DisplayLease, Job
from skuldbot_runner.visual_keywords import SkuldBotVisualKeywords
from skuldbot_runner.windows_session_pool import (
    WindowsInteractiveSessionPool,
    slots_from_environment,
)

RUN_DISPLAY_TESTS = os.environ.get("SKULDBOT_VISUAL_ACTION_INTEGRATION") == "1"
RUN_XVFB_TESTS = os.environ.get("SKULDBOT_XVFB_VISUAL_ACTION_INTEGRATION") == "1"
RUN_WINDOWS_CONCURRENT_TESTS = (
    os.environ.get("SKULDBOT_WINDOWS_CONCURRENT_VISUAL_INTEGRATION") == "1"
)


@pytest.fixture(autouse=True)
def disable_provider_backed_upload_for_display_adapter_tests(monkeypatch):
    monkeypatch.setenv("SKULDBOT_EVIDENCE_ARTIFACT_UPLOAD_REQUIRED", "false")


@contextmanager
def display_lease_environment(job: Job):
    lease_environment = (
        build_display_lease_environment(job.display_lease)
        if job.display_lease is not None
        else {}
    )
    previous_values = {key: os.environ.get(key) for key in lease_environment}
    os.environ.update(lease_environment)
    try:
        yield
    finally:
        for key, previous_value in previous_values.items():
            if previous_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous_value


def _display_lease(
    *actions: str,
    run_id: str = "run-visual-integration",
    runtime_plane: str = "linux_virtual_display",
    mode: str = "unattended",
) -> DisplayLease:
    return DisplayLease.model_validate(
        {
            "leaseId": f"lease-{run_id}",
            "state": "active",
            "runnerId": "runner-visual-integration",
            "grantedAt": "2026-06-03T10:00:00Z",
            "expiresAt": "2026-06-03T10:10:00Z",
            "request": {
                "leaseRequestId": f"lease-request-{run_id}",
                "tenantId": "tenant-visual-integration",
                "runId": run_id,
                "stepId": "step-visual-integration",
                "runtimePlane": runtime_plane,
                "mode": mode,
                "requiredCapabilities": ["graphical_display"],
                "requiredVisualActions": list(actions),
                "sessionCredentialRefs": [],
                "reason": "visual runtime integration",
            },
            "session": {
                "sessionId": "session-visual-integration",
                "tenantId": "tenant-visual-integration",
                "runnerId": "runner-visual-integration",
                "runtimePlane": runtime_plane,
                "mode": mode,
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


@pytest.mark.skipif(
    not RUN_DISPLAY_TESTS,
    reason="Set SKULDBOT_VISUAL_ACTION_INTEGRATION=1 with a real display.",
)
def test_desktop_screenshot_captures_real_display(tmp_path):
    pytest.importorskip("RPA.Desktop")
    output_path = tmp_path / "screen.png"
    job = Job(id="run-visual-integration", display_lease=_display_lease("screenshot"))

    with display_lease_environment(job):
        result = SkuldBotVisualKeywords().desktop_screenshot(str(output_path))

    assert result["action"] == "screenshot"
    assert result["success"] is True
    assert result["artifactPath"] == str(output_path)
    assert result["sizeBytes"] > 0
    assert len(result["checksumSha256"]) == 64


@pytest.mark.skipif(
    not RUN_XVFB_TESTS,
    reason="Set SKULDBOT_XVFB_VISUAL_ACTION_INTEGRATION=1 to run against real Xvfb.",
)
def test_xvfb_display_executes_visual_keywords_with_lease(tmp_path):
    pytest.importorskip("RPA.Desktop")
    if shutil.which("Xvfb") is None:
        pytest.skip("Xvfb executable is not available.")

    session = LinuxVirtualDisplaySession(
        LinuxVirtualDisplayConfig(
            display=os.environ.get("SKULDBOT_XVFB_TEST_DISPLAY", ":96"),
            startup_timeout_seconds=3.0,
        )
    )
    screenshot_path = tmp_path / "xvfb-screen.png"
    job = Job(
        id="run-xvfb-visual-integration",
        display_lease=_display_lease(
            "screenshot",
            "type_text",
            "hotkey",
            "image_click",
            "wait_image",
        ),
    )

    try:
        session.start()
        with display_lease_environment(job):
            keywords = SkuldBotVisualKeywords()
            screenshot = keywords.desktop_screenshot(str(screenshot_path))
            typed = keywords.desktop_type_text("SkuldBot Xvfb")
            hotkey = keywords.desktop_hotkey("ctrl", "a")
            waited = keywords.desktop_wait_image(str(screenshot_path), timeout_seconds=3)
            clicked = keywords.desktop_image_click(str(screenshot_path))
    finally:
        session.stop()

    assert screenshot["success"] is True
    assert screenshot["artifactPath"] == str(screenshot_path)
    assert screenshot["sizeBytes"] > 0
    assert len(screenshot["checksumSha256"]) == 64
    assert typed["success"] is True
    assert hotkey["success"] is True
    assert waited["success"] is True
    assert clicked["success"] is True


VISUAL_RUNNER_SCRIPT = r"""
import json
import os
import sys

from skuldbot_runner.visual_keywords import SkuldBotVisualKeywords

artifact_path = sys.argv[1]
keywords = SkuldBotVisualKeywords()
screenshot = keywords.desktop_screenshot(artifact_path)
typed = keywords.desktop_type_text("SkuldBot high density")
hotkey = keywords.desktop_hotkey("ctrl", "a")
waited = keywords.desktop_wait_image(artifact_path, timeout_seconds=3)
clicked = keywords.desktop_image_click(artifact_path)
print(
    json.dumps(
        {
            "screenshot": screenshot,
            "typed": typed,
            "hotkey": hotkey,
            "waited": waited,
            "clicked": clicked,
            "sessionId": os.environ.get("SKULDBOT_WINDOWS_SESSION_ID", ""),
            "robotUserRef": os.environ.get("SKULDBOT_WINDOWS_ROBOT_USER_REF", ""),
            "attached": os.environ.get("SKULDBOT_WINDOWS_SESSION_ATTACHED", ""),
        }
    )
)
"""


@pytest.mark.skipif(
    not RUN_XVFB_TESTS,
    reason="Set SKULDBOT_XVFB_VISUAL_ACTION_INTEGRATION=1 to run against real Xvfb.",
)
def test_xvfb_pool_runs_two_visual_jobs_with_isolated_evidence(tmp_path):
    pytest.importorskip("RPA.Desktop")
    if shutil.which("Xvfb") is None:
        pytest.skip("Xvfb executable is not available.")

    pool = LinuxVirtualDisplayPool(
        base_config=LinuxVirtualDisplayConfig(
            display=os.environ.get("SKULDBOT_XVFB_CONCURRENT_TEST_DISPLAY", ":130"),
            startup_timeout_seconds=3.0,
        ),
        max_sessions=2,
        base_environment={},
    )
    run_ids = ("run-xvfb-a", "run-xvfb-b")
    actions = ("screenshot", "type_text", "hotkey", "image_click", "wait_image")

    with ExitStack() as stack:
        leases = [stack.enter_context(pool.acquire(run_id)) for run_id in run_ids]
        processes: list[subprocess.Popen[str]] = []
        artifact_paths = []

        for run_id, lease in zip(run_ids, leases, strict=True):
            artifact_path = tmp_path / run_id / "screen.png"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_paths.append(artifact_path)
            display_lease = _display_lease(*actions, run_id=run_id)
            env = dict(os.environ)
            env.update(lease.environment)
            env.update(build_display_lease_environment(display_lease))
            processes.append(
                subprocess.Popen(  # noqa: S603
                    [sys.executable, "-c", VISUAL_RUNNER_SCRIPT, str(artifact_path)],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )

        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))

    assert leases[0].display != leases[1].display
    assert artifact_paths[0] != artifact_paths[1]
    for result, artifact_path in zip(results, artifact_paths, strict=True):
        screenshot = result["screenshot"]
        assert screenshot["success"] is True
        assert screenshot["artifactPath"] == str(artifact_path)
        assert screenshot["sizeBytes"] > 0
        assert len(screenshot["checksumSha256"]) == 64
        assert result["typed"]["success"] is True
        assert result["hotkey"]["success"] is True
        assert result["waited"]["success"] is True
        assert result["clicked"]["success"] is True


@pytest.mark.skipif(
    not RUN_WINDOWS_CONCURRENT_TESTS,
    reason=(
        "Set SKULDBOT_WINDOWS_CONCURRENT_VISUAL_INTEGRATION=1 on a Windows host "
        "with two active robot sessions and the host service running."
    ),
)
def test_windows_pool_runs_two_visual_jobs_with_isolated_sessions_and_evidence(tmp_path):
    if sys.platform != "win32":
        pytest.skip("Windows concurrent visual integration requires Windows.")
    pytest.importorskip("RPA.Desktop")

    slots = slots_from_environment(os.environ)
    if len(slots) < 2:
        pytest.skip("Configure at least two Windows session pool slots.")

    pool = WindowsInteractiveSessionPool(slots=slots[:2], base_environment=os.environ)
    run_ids = ("run-windows-a", "run-windows-b")
    actions = ("screenshot", "type_text", "hotkey", "image_click", "wait_image")

    with ExitStack() as stack:
        leases = [stack.enter_context(pool.acquire(run_id)) for run_id in run_ids]
        processes: list[subprocess.Popen[str]] = []
        artifact_paths = []

        for run_id, lease in zip(run_ids, leases, strict=True):
            artifact_path = tmp_path / run_id / "screen.png"
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_paths.append(artifact_path)
            display_lease = _display_lease(
                *actions,
                run_id=run_id,
                runtime_plane="windows_interactive",
                mode="unattended",
            )
            env = dict(os.environ)
            env.update(lease.environment)
            env.update(build_display_lease_environment(display_lease))
            env["SKULDBOT_EVIDENCE_ARTIFACT_UPLOAD_REQUIRED"] = "false"
            processes.append(
                subprocess.Popen(  # noqa: S603
                    [
                        sys.executable,
                        "-m",
                        "skuldbot_runner.windows_session_broker",
                        "--session-id",
                        lease.slot.session_id,
                        "--robot-user-ref",
                        lease.slot.robot_user_ref,
                        "--credential-ref-key",
                        lease.slot.credential_ref_key,
                        "--",
                        sys.executable,
                        "-c",
                        VISUAL_RUNNER_SCRIPT,
                        str(artifact_path),
                    ],
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            )

        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=45)
            assert process.returncode == 0, (
                f"visual worker failed with exit={process.returncode}\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{stderr}"
            )
            results.append(json.loads(stdout))

    assert leases[0].slot.session_id != leases[1].slot.session_id
    assert leases[0].slot.robot_user_ref != leases[1].slot.robot_user_ref
    assert artifact_paths[0] != artifact_paths[1]
    assert {result["sessionId"] for result in results} == {
        lease.slot.session_id for lease in leases
    }
    assert {result["robotUserRef"] for result in results} == {
        lease.slot.robot_user_ref for lease in leases
    }
    for result, artifact_path in zip(results, artifact_paths, strict=True):
        assert result["attached"] == "1"
        screenshot = result["screenshot"]
        assert screenshot["success"] is True
        assert screenshot["artifactPath"] == str(artifact_path)
        assert screenshot["sizeBytes"] > 0
        assert len(screenshot["checksumSha256"]) == 64
        assert result["typed"]["success"] is True
        assert result["hotkey"]["success"] is True
        assert result["waited"]["success"] is True
        assert result["clicked"]["success"] is True
