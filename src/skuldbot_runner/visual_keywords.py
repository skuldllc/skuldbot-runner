# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Robot Framework visual-action keywords with fail-closed graphical checks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .graphical_runtime import detect_graphical_capabilities
from .models import (
    GraphicalDisplayState,
    GraphicalRunnerCapabilities,
    VisualActionKind,
)

DISPLAY_READY_STATES = {
    GraphicalDisplayState.AVAILABLE,
    GraphicalDisplayState.ACTIVE,
}


class VisualActionError(RuntimeError):
    """Raised when a visual action cannot execute safely."""


@dataclass(frozen=True)
class VisualActionResult:
    """Structured result emitted by visual keyword methods."""

    action: VisualActionKind
    success: bool
    artifact_path: str | None = None
    message: str | None = None

    def to_robot_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "success": self.success,
            "artifactPath": self.artifact_path,
            "message": self.message,
        }


def require_visual_action(
    capabilities: GraphicalRunnerCapabilities | None,
    action: VisualActionKind,
) -> None:
    """Fail closed unless the current runner explicitly supports this action."""

    if capabilities is None:
        raise VisualActionError(
            f"Graphical capability is required for visual action {action.value}."
        )

    if not capabilities.has_display:
        raise VisualActionError("Graphical display is not available for this runner.")

    if capabilities.display.locked:
        raise VisualActionError("Graphical display is locked.")

    if not capabilities.display.connected:
        raise VisualActionError("Graphical display is disconnected.")

    if capabilities.display.state not in DISPLAY_READY_STATES:
        raise VisualActionError(
            f"Graphical display state is {capabilities.display.state.value}."
        )

    if action not in capabilities.supported_visual_actions:
        raise VisualActionError(
            f"Visual action {action.value} is not declared by this runner."
        )


class SkuldBotVisualKeywords:
    """Robot Framework keyword library for SkuldBot visual desktop actions."""

    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    def __init__(self) -> None:
        self._capabilities = detect_graphical_capabilities()

    def desktop_screenshot(self, output_path: str) -> dict[str, Any]:
        """Capture a screenshot of the active graphical display."""

        require_visual_action(self._capabilities, VisualActionKind.SCREENSHOT)
        resolved_path = self._ensure_output_path(output_path)
        backend = self._desktop_backend()
        backend.take_screenshot(str(resolved_path))
        return VisualActionResult(
            action=VisualActionKind.SCREENSHOT,
            success=True,
            artifact_path=str(resolved_path),
        ).to_robot_dict()

    def desktop_type_text(self, text: str) -> dict[str, Any]:
        """Type text into the active graphical session."""

        require_visual_action(self._capabilities, VisualActionKind.TYPE_TEXT)
        backend = self._desktop_backend()
        backend.type_text(text)
        return VisualActionResult(
            action=VisualActionKind.TYPE_TEXT,
            success=True,
            message="Text typed.",
        ).to_robot_dict()

    def desktop_hotkey(self, *keys: str) -> dict[str, Any]:
        """Send a hotkey combination to the active graphical session."""

        require_visual_action(self._capabilities, VisualActionKind.HOTKEY)
        if not keys:
            raise VisualActionError("At least one key is required.")
        backend = self._desktop_backend()
        backend.press_keys(*keys)
        return VisualActionResult(
            action=VisualActionKind.HOTKEY,
            success=True,
            message="Hotkey sent.",
        ).to_robot_dict()

    def desktop_image_click(self, image_path: str) -> dict[str, Any]:
        """Click the first matching image in the active graphical session."""

        require_visual_action(self._capabilities, VisualActionKind.IMAGE_CLICK)
        self._require_existing_file(image_path)
        backend = self._desktop_backend()
        backend.click(image_path)
        return VisualActionResult(
            action=VisualActionKind.IMAGE_CLICK,
            success=True,
            artifact_path=image_path,
        ).to_robot_dict()

    def desktop_wait_image(
        self,
        image_path: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Wait until an image appears in the active graphical session."""

        require_visual_action(self._capabilities, VisualActionKind.WAIT_IMAGE)
        self._require_existing_file(image_path)
        backend = self._desktop_backend()
        backend.wait_for_element(image_path, timeout=timeout_seconds)
        return VisualActionResult(
            action=VisualActionKind.WAIT_IMAGE,
            success=True,
            artifact_path=image_path,
        ).to_robot_dict()

    def document_ocr_region(self, image_path: str, region: str | None = None) -> dict[str, Any]:
        """Reject OCR execution until provider-backed OCR is wired."""

        require_visual_action(self._capabilities, VisualActionKind.OCR_REGION)
        self._require_existing_file(image_path)
        raise VisualActionError(
            "document.ocr requires provider-backed OCR integration before execution."
        )

    def _desktop_backend(self) -> Any:
        try:
            from RPA.Desktop import Desktop
        except ImportError as exc:
            raise VisualActionError(
                "RPA.Desktop is required for visual desktop actions."
            ) from exc

        return Desktop()

    @staticmethod
    def _ensure_output_path(output_path: str) -> Path:
        path = Path(output_path).expanduser()
        if not path.name:
            raise VisualActionError("Output path must include a filename.")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _require_existing_file(path: str) -> None:
        if not Path(path).expanduser().is_file():
            raise VisualActionError(f"File not found: {path}")
