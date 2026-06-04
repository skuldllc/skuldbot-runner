# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Contract tests for graphical runtime package and image dependencies."""

from __future__ import annotations

from pathlib import Path

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_visual_image_matching_dependency_is_required():
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    dependencies = project["project"]["dependencies"]

    assert any(
        dependency.startswith("rpaframework-recognition")
        for dependency in dependencies
    )


def test_runner_image_declares_linux_virtual_display_system_dependencies():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()

    required_packages = {
        "xvfb",
        "scrot",
        "xauth",
        "x11-utils",
        "libgl1",
        "libglib2.0-0",
        "libgtk-3-0",
        "libsm6",
        "libxext6",
        "libxi6",
        "libxrandr2",
        "libxrender1",
        "libxss1",
        "libxtst6",
    }

    missing = sorted(package for package in required_packages if package not in dockerfile)
    assert missing == []


def test_runner_image_vendors_visual_recognition_wheels_for_offline_install():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text()

    assert "AS visual-dependency-wheels" in dockerfile
    assert (
        'pip wheel --no-cache-dir --wheel-dir /wheels "rpaframework-recognition>=5.0.0"'
        in dockerfile
    )
    assert "COPY --from=visual-dependency-wheels /wheels /wheels-visual" in dockerfile
    assert "--find-links=/wheels-visual" in dockerfile
    assert "find_spec('RPA.recognition')" in dockerfile
