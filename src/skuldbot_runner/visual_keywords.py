# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Robot Framework visual-action keywords with fail-closed graphical checks."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifact_uploader import (
    ArtifactUploadError,
    OrchestratorArtifactUploader,
    artifact_upload_required,
)
from .graphical_runtime import (
    DisplayLeaseRuntimeContext,
    detect_graphical_capabilities,
    read_display_lease_context,
)
from .models import (
    GraphicalDisplayState,
    GraphicalRunnerCapabilities,
    VisualActionKind,
)
from .ocr_provider import OcrProviderConfig, OcrProviderError, ProviderBackedOcrClient
from .staging_artifacts import (
    STAGING_ROOT_ENV,
    is_path_inside_root,
    record_uploaded_staging_artifact,
    require_evidence_staging_path,
)
from .visual_adapter import RpaDesktopVisualAdapter, VisualAdapterError

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
    artifact_id: str | None = None
    artifact_path: str | None = None
    checksum_sha256: str | None = None
    size_bytes: int | None = None
    mime_type: str | None = None
    classification: str | None = None
    redaction_applied: bool | None = None
    message: str | None = None

    def to_robot_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "success": self.success,
            "artifactId": self.artifact_id,
            "artifactPath": self.artifact_path,
            "checksumSha256": self.checksum_sha256,
            "sizeBytes": self.size_bytes,
            "mimeType": self.mime_type,
            "classification": self.classification,
            "redactionApplied": self.redaction_applied,
            "message": self.message,
        }


def require_visual_action(
    capabilities: GraphicalRunnerCapabilities | None,
    action: VisualActionKind,
    lease_context: DisplayLeaseRuntimeContext | None,
) -> None:
    """Fail closed unless the current runner explicitly supports this action."""

    if lease_context is None:
        raise VisualActionError(
            f"Display lease is required for visual action {action.value}."
        )

    if not lease_context.is_active:
        raise VisualActionError(
            f"Display lease {lease_context.lease_id} is not active."
        )

    if not lease_context.allows(action):
        raise VisualActionError(
            f"Visual action {action.value} is not granted by display lease."
        )

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

    if lease_context.runtime_plane not in capabilities.supported_runtime_planes:
        raise VisualActionError(
            f"Runtime plane {lease_context.runtime_plane.value} is not declared by this runner."
        )

    if lease_context.mode not in capabilities.supported_session_modes:
        raise VisualActionError(
            f"Session mode {lease_context.mode.value} is not declared by this runner."
        )


class SkuldBotVisualKeywords:
    """Robot Framework keyword library for SkuldBot visual desktop actions."""

    ROBOT_LIBRARY_SCOPE = "GLOBAL"

    def __init__(self) -> None:
        self._capabilities = detect_graphical_capabilities()
        self._lease_context = read_display_lease_context()

    def desktop_screenshot(self, output_path: str) -> dict[str, Any]:
        """Capture a screenshot of the active graphical display."""

        require_visual_action(
            self._capabilities,
            VisualActionKind.SCREENSHOT,
            self._lease_context,
        )
        resolved_path = self._ensure_output_path(output_path)
        artifact = self._execute_adapter(lambda adapter: adapter.screenshot(resolved_path))
        uploaded = self._upload_artifact(
            action=VisualActionKind.SCREENSHOT,
            artifact_path=Path(artifact.path),
            checksum_sha256=artifact.checksum_sha256,
            mime_type="image/png",
            cleanup_staging=True,
        )
        return VisualActionResult(
            action=VisualActionKind.SCREENSHOT,
            success=True,
            artifact_id=uploaded.artifact_id if uploaded else None,
            artifact_path=None if uploaded else artifact.path,
            checksum_sha256=artifact.checksum_sha256,
            size_bytes=artifact.size_bytes,
            mime_type=uploaded.mime_type if uploaded else "image/png",
            classification=uploaded.classification if uploaded else None,
            redaction_applied=uploaded.redaction_applied if uploaded else None,
        ).to_robot_dict()

    def desktop_type_text(self, text: str) -> dict[str, Any]:
        """Type text into the active graphical session."""

        require_visual_action(
            self._capabilities,
            VisualActionKind.TYPE_TEXT,
            self._lease_context,
        )
        self._execute_adapter(lambda adapter: adapter.type_text(text))
        return VisualActionResult(
            action=VisualActionKind.TYPE_TEXT,
            success=True,
            message="Text typed.",
        ).to_robot_dict()

    def desktop_hotkey(self, *keys: str) -> dict[str, Any]:
        """Send a hotkey combination to the active graphical session."""

        require_visual_action(
            self._capabilities,
            VisualActionKind.HOTKEY,
            self._lease_context,
        )
        if not keys:
            raise VisualActionError("At least one key is required.")
        self._execute_adapter(lambda adapter: adapter.hotkey(tuple(keys)))
        return VisualActionResult(
            action=VisualActionKind.HOTKEY,
            success=True,
            message="Hotkey sent.",
        ).to_robot_dict()

    def desktop_image_click(self, image_path: str) -> dict[str, Any]:
        """Click the first matching image in the active graphical session."""

        require_visual_action(
            self._capabilities,
            VisualActionKind.IMAGE_CLICK,
            self._lease_context,
        )
        resolved_path = self._require_existing_file(image_path)
        self._require_reference_outside_staging(resolved_path)
        self._execute_adapter(lambda adapter: adapter.image_click(resolved_path))
        return VisualActionResult(
            action=VisualActionKind.IMAGE_CLICK,
            success=True,
            artifact_path=str(resolved_path),
        ).to_robot_dict()

    def desktop_wait_image(
        self,
        image_path: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Wait until an image appears in the active graphical session."""

        require_visual_action(
            self._capabilities,
            VisualActionKind.WAIT_IMAGE,
            self._lease_context,
        )
        resolved_path = self._require_existing_file(image_path)
        self._require_reference_outside_staging(resolved_path)
        self._execute_adapter(
            lambda adapter: adapter.wait_image(
                resolved_path,
                timeout_seconds=timeout_seconds,
            )
        )
        return VisualActionResult(
            action=VisualActionKind.WAIT_IMAGE,
            success=True,
            artifact_path=str(resolved_path),
        ).to_robot_dict()

    def document_ocr_region(self, image_path: str, region: str | None = None) -> dict[str, Any]:
        """Extract text through the configured provider-backed OCR integration."""

        require_visual_action(
            self._capabilities,
            VisualActionKind.OCR_REGION,
            self._lease_context,
        )
        resolved_path = self._require_existing_file(image_path)
        try:
            result = ProviderBackedOcrClient(
                OcrProviderConfig.from_environment()
            ).ocr_region(resolved_path, region=region)
        except OcrProviderError as exc:
            raise VisualActionError(str(exc)) from exc

        payload = result.to_robot_dict()
        payload["action"] = VisualActionKind.OCR_REGION.value
        payload["success"] = True
        uploaded = self._upload_artifact(
            action=VisualActionKind.OCR_REGION,
            artifact_path=Path(result.source_artifact.path),
            checksum_sha256=result.source_artifact.checksum_sha256,
            mime_type="application/octet-stream",
            redaction_applied=result.redaction_applied,
        )
        if uploaded:
            payload["artifactId"] = uploaded.artifact_id
            payload["artifactPath"] = None
            payload["classification"] = uploaded.classification
        return payload

    @staticmethod
    def _adapter() -> RpaDesktopVisualAdapter:
        try:
            return RpaDesktopVisualAdapter()
        except VisualAdapterError as exc:
            raise VisualActionError(str(exc)) from exc

    @classmethod
    def _execute_adapter(cls, operation: Any) -> Any:
        try:
            return operation(cls._adapter())
        except VisualAdapterError as exc:
            raise VisualActionError(str(exc)) from exc

    @staticmethod
    def _ensure_output_path(output_path: str) -> Path:
        path = Path(output_path).expanduser()
        if not path.name:
            raise VisualActionError("Output path must include a filename.")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _require_existing_file(path: str) -> Path:
        resolved_path = Path(path).expanduser()
        if not resolved_path.is_file():
            raise VisualActionError(f"File not found: {path}")
        return resolved_path

    @staticmethod
    def _require_reference_outside_staging(path: Path) -> None:
        staging_root = os.environ.get(STAGING_ROOT_ENV, "").strip()
        if not staging_root:
            return
        if is_path_inside_root(path, Path(staging_root).expanduser()):
            raise VisualActionError(
                "Visual reference images must be outside the evidence staging folder."
            )

    def _upload_artifact(
        self,
        *,
        action: VisualActionKind,
        artifact_path: Path,
        checksum_sha256: str,
        mime_type: str,
        redaction_applied: bool | None = None,
        cleanup_staging: bool = False,
    ) -> Any | None:
        if not artifact_upload_required():
            return None

        if self._lease_context is None:
            raise VisualActionError("Display lease is required for evidence upload.")

        if cleanup_staging:
            try:
                require_evidence_staging_path(artifact_path)
            except ValueError as exc:
                raise VisualActionError(str(exc)) from exc

        try:
            uploaded = OrchestratorArtifactUploader().upload(
                run_id=self._lease_context.run_id,
                action=action.value,
                artifact_path=artifact_path,
                checksum_sha256=checksum_sha256,
                mime_type=mime_type,
                redaction_applied=redaction_applied,
            )
            if cleanup_staging:
                record_uploaded_staging_artifact(
                    artifact_path=artifact_path,
                    artifact_id=uploaded.artifact_id,
                    action=action.value,
                )
            return uploaded
        except ArtifactUploadError as exc:
            raise VisualActionError(str(exc)) from exc
