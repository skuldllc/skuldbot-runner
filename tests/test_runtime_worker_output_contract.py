# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

from skuldbot_runner.runtime_worker import _sanitize_runtime_output


def test_runtime_worker_strips_robot_report_paths_from_outputs():
    output = _sanitize_runtime_output(
        {
            "log_html": "/tmp/skuldbot-xvfb-e2e/run/bot/output/log.html",
            "output_xml": "/tmp/skuldbot-xvfb-e2e/run/bot/output/output.xml",
            "report_html": "/tmp/skuldbot-xvfb-e2e/run/bot/output/report.html",
            "business_result": "approved",
            "details": {
                "download_path": "/tmp/skuldbot-xvfb-e2e/download.pdf",
                "policy": "internal",
            },
            "windows_path": "C:\\Users\\bot\\AppData\\Local\\Temp\\report.html",
            "items": ["kept", "/var/tmp/local-artifact.png"],
        }
    )

    assert output == {
        "business_result": "approved",
        "details": {"policy": "internal"},
        "items": ["kept"],
    }
