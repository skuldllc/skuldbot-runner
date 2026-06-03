# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import os
import shutil

import pytest

from skuldbot_runner.executor import display_lease_environment
from skuldbot_runner.linux_virtual_display import (
    LinuxVirtualDisplayConfig,
    LinuxVirtualDisplaySession,
)
from skuldbot_runner.models import DisplayLease, Job
from skuldbot_runner.visual_keywords import SkuldBotVisualKeywords

RUN_DISPLAY_TESTS = os.environ.get("SKULDBOT_VISUAL_ACTION_INTEGRATION") == "1"
RUN_XVFB_TESTS = os.environ.get("SKULDBOT_XVFB_VISUAL_ACTION_INTEGRATION") == "1"


def _display_lease(*actions: str) -> DisplayLease:
    return DisplayLease.model_validate(
        {
            "leaseId": "lease-visual-integration",
            "state": "active",
            "runnerId": "runner-visual-integration",
            "grantedAt": "2026-06-03T10:00:00Z",
            "expiresAt": "2026-06-03T10:10:00Z",
            "request": {
                "leaseRequestId": "lease-request-visual-integration",
                "tenantId": "tenant-visual-integration",
                "runId": "run-visual-integration",
                "stepId": "step-visual-integration",
                "runtimePlane": "linux_virtual_display",
                "mode": "unattended",
                "requiredCapabilities": ["graphical_display"],
                "requiredVisualActions": list(actions),
                "sessionCredentialRefs": [],
                "reason": "visual runtime integration",
            },
            "session": {
                "sessionId": "session-visual-integration",
                "tenantId": "tenant-visual-integration",
                "runnerId": "runner-visual-integration",
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
