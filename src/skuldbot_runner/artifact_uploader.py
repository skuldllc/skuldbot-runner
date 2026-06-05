# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Provider-backed run artifact upload through the orchestrator boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ArtifactUploadError(RuntimeError):
    """Raised when a run artifact cannot be recorded as provider-backed evidence."""


@dataclass(frozen=True)
class UploadedArtifact:
    """Provider-backed artifact reference returned by the orchestrator."""

    artifact_id: str
    action: str
    checksum_sha256: str
    size_bytes: int
    mime_type: str
    classification: str
    redaction_applied: bool | None = None

    def to_robot_dict(self) -> dict[str, Any]:
        return {
            "artifactId": self.artifact_id,
            "action": self.action,
            "checksumSha256": self.checksum_sha256,
            "sizeBytes": self.size_bytes,
            "mimeType": self.mime_type,
            "classification": self.classification,
            "redactionApplied": self.redaction_applied,
        }


def artifact_upload_required(environment: dict[str, str] | None = None) -> bool:
    """Return whether visual artifacts must be uploaded to orchestrator storage."""

    env = environment if environment is not None else os.environ
    value = env.get("SKULDBOT_EVIDENCE_ARTIFACT_UPLOAD_REQUIRED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


class OrchestratorArtifactUploader:
    """Uploads artifact bytes to the runner-agent artifact endpoint."""

    def __init__(self, environment: dict[str, str] | None = None) -> None:
        env = environment if environment is not None else os.environ
        self._base_url = env.get("SKULDBOT_ORCHESTRATOR_URL", "").rstrip("/")
        self._api_key = env.get("SKULDBOT_API_KEY", "")
        self._classification = env.get("SKULDBOT_EVIDENCE_CLASSIFICATION", "").strip()
        self._timeout_seconds = float(
            env.get("SKULDBOT_EVIDENCE_UPLOAD_TIMEOUT_SECONDS", "30")
        )

    def upload(
        self,
        *,
        run_id: str,
        action: str,
        artifact_path: Path,
        checksum_sha256: str,
        mime_type: str,
        node_id: str | None = None,
        step_id: str | None = None,
        redaction_applied: bool | None = None,
    ) -> UploadedArtifact:
        """Upload an artifact file and return the provider-backed reference."""

        if not self._base_url:
            raise ArtifactUploadError("SKULDBOT_ORCHESTRATOR_URL is required for evidence upload.")
        if not self._api_key:
            raise ArtifactUploadError("SKULDBOT_API_KEY is required for evidence upload.")
        if not self._classification:
            raise ArtifactUploadError(
                "SKULDBOT_EVIDENCE_CLASSIFICATION is required for evidence upload."
            )
        if not artifact_path.is_file():
            raise ArtifactUploadError(f"Artifact file does not exist: {artifact_path}")

        try:
            import httpx
        except ImportError as exc:
            raise ArtifactUploadError("httpx is required for evidence upload.") from exc

        data: dict[str, str] = {
            "action": action,
            "classification": self._classification,
            "expectedChecksumSha256": checksum_sha256,
        }
        if node_id:
            data["nodeId"] = node_id
        if step_id:
            data["stepId"] = step_id
        if redaction_applied is not None:
            data["redactionApplied"] = "true" if redaction_applied else "false"

        with artifact_path.open("rb") as artifact_file:
            try:
                response = httpx.post(
                    f"{self._base_url}/runner-agent/runs/{run_id}/artifacts",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data=data,
                    files={
                        "artifact": (
                            artifact_path.name,
                            artifact_file,
                            mime_type,
                        )
                    },
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ArtifactUploadError(f"Evidence artifact upload failed: {exc}") from exc

        payload = response.json()
        artifact_id = str(payload.get("artifactId", "")).strip()
        if not artifact_id:
            raise ArtifactUploadError("Evidence artifact upload did not return artifactId.")

        return UploadedArtifact(
            artifact_id=artifact_id,
            action=str(payload.get("action") or action),
            checksum_sha256=str(payload.get("checksumSha256") or checksum_sha256),
            size_bytes=int(payload.get("sizeBytes") or artifact_path.stat().st_size),
            mime_type=str(payload.get("mimeType") or mime_type),
            classification=str(payload.get("classification") or self._classification),
            redaction_applied=payload.get("redactionApplied"),
        )
