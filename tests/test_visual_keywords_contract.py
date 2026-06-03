# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import pytest

from skuldbot_runner.graphical_runtime import (
    GraphicalProbeInput,
    build_graphical_capabilities,
)
from skuldbot_runner.models import (
    GraphicalDisplayState,
    VisualActionKind,
)
from skuldbot_runner.visual_adapter import VisualArtifact
from skuldbot_runner.visual_keywords import (
    VisualActionError,
    VisualActionResult,
    require_visual_action,
)


def _ready_capabilities():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Linux",
            environment={"DISPLAY": ":99"},
        )
    )
    assert capability is not None
    return capability


def test_visual_action_requires_declared_graphical_capability():
    with pytest.raises(VisualActionError, match="Graphical capability is required"):
        require_visual_action(None, VisualActionKind.SCREENSHOT)


def test_visual_action_rejects_locked_display():
    capability = build_graphical_capabilities(
        GraphicalProbeInput(
            platform_system="Linux",
            environment={"DISPLAY": ":99", "SKULDBOT_DISPLAY_LOCKED": "true"},
        )
    )
    assert capability is not None

    with pytest.raises(VisualActionError, match="not available"):
        require_visual_action(capability, VisualActionKind.SCREENSHOT)


def test_visual_action_rejects_non_ready_display_state():
    capability = _ready_capabilities()
    capability.display.state = GraphicalDisplayState.STALE

    with pytest.raises(VisualActionError, match="stale"):
        require_visual_action(capability, VisualActionKind.SCREENSHOT)


def test_visual_action_rejects_undeclared_action():
    capability = _ready_capabilities()
    capability.supported_visual_actions = [VisualActionKind.SCREENSHOT]

    with pytest.raises(VisualActionError, match="not declared"):
        require_visual_action(capability, VisualActionKind.IMAGE_CLICK)


def test_visual_action_allows_declared_ready_action():
    require_visual_action(_ready_capabilities(), VisualActionKind.SCREENSHOT)


def test_visual_action_result_uses_orchestrator_field_names():
    result = VisualActionResult(
        action=VisualActionKind.SCREENSHOT,
        success=True,
        artifact_path="/tmp/run/screen.png",
        checksum_sha256="abc123",
        size_bytes=42,
        message="captured",
    )

    assert result.to_robot_dict() == {
        "action": "screenshot",
        "success": True,
        "artifactPath": "/tmp/run/screen.png",
        "checksumSha256": "abc123",
        "sizeBytes": 42,
        "message": "captured",
    }


def test_visual_artifact_computes_checksum_and_size(tmp_path):
    artifact_path = tmp_path / "frame.bin"
    artifact_path.write_bytes(b"skuldbot-frame")

    artifact = VisualArtifact.from_file(artifact_path)

    assert artifact.path == str(artifact_path)
    assert artifact.size_bytes == 14
    assert artifact.checksum_sha256 == (
        "f3d1d3cb4773b0ead078710ee812a1033dc750f9265b190dc77c0bd25a6bbfce"
    )
