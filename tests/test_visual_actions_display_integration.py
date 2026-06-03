# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import os

import pytest

from skuldbot_runner.visual_keywords import SkuldBotVisualKeywords

RUN_DISPLAY_TESTS = os.environ.get("SKULDBOT_VISUAL_ACTION_INTEGRATION") == "1"


@pytest.mark.skipif(
    not RUN_DISPLAY_TESTS,
    reason="Set SKULDBOT_VISUAL_ACTION_INTEGRATION=1 with a real display.",
)
def test_desktop_screenshot_captures_real_display(tmp_path):
    pytest.importorskip("RPA.Desktop")
    output_path = tmp_path / "screen.png"

    result = SkuldBotVisualKeywords().desktop_screenshot(str(output_path))

    assert result["action"] == "screenshot"
    assert result["success"] is True
    assert result["artifactPath"] == str(output_path)
    assert result["sizeBytes"] > 0
    assert len(result["checksumSha256"]) == 64
