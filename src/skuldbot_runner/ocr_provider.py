# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Provider-backed OCR client for graphical document extraction."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .visual_adapter import VisualArtifact

DEFAULT_TIMEOUT_SECONDS = 30.0
SUPPORTED_PROVIDER_KIND = "generic_http"


class OcrProviderError(RuntimeError):
    """Raised when OCR cannot run through the configured provider."""


@dataclass(frozen=True)
class OcrProviderConfig:
    """OCR provider configuration resolved from runner bootstrap configuration."""

    provider_kind: str
    endpoint_url: str
    secret_ref_key: str
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> "OcrProviderConfig":
        env = environment or os.environ
        provider_kind = env.get("SKULDBOT_OCR_PROVIDER_KIND", SUPPORTED_PROVIDER_KIND)
        endpoint_url = env.get("SKULDBOT_OCR_PROVIDER_ENDPOINT", "").strip()
        secret_ref_key = env.get("SKULDBOT_OCR_PROVIDER_SECRET_REF", "").strip()
        timeout_seconds = _read_positive_float(
            env.get("SKULDBOT_OCR_PROVIDER_TIMEOUT_SECONDS"),
            DEFAULT_TIMEOUT_SECONDS,
        )

        if provider_kind != SUPPORTED_PROVIDER_KIND:
            raise OcrProviderError(f"OCR provider kind is not supported: {provider_kind}")

        if not endpoint_url:
            raise OcrProviderError("OCR provider endpoint is not configured.")

        if not secret_ref_key:
            raise OcrProviderError("OCR provider secretRef is not configured.")

        return cls(
            provider_kind=provider_kind,
            endpoint_url=endpoint_url,
            secret_ref_key=secret_ref_key,
            timeout_seconds=timeout_seconds,
        )


@dataclass(frozen=True)
class OcrProviderResult:
    """OCR output safe for runner logs and Robot Framework variables."""

    text: str
    confidence: float | None
    redaction_applied: bool
    provider_kind: str
    source_artifact: VisualArtifact
    blocks: list[dict[str, Any]] = field(default_factory=list)

    def to_robot_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "redactionApplied": self.redaction_applied,
            "providerKind": self.provider_kind,
            "artifactPath": self.source_artifact.path,
            "checksumSha256": self.source_artifact.checksum_sha256,
            "sizeBytes": self.source_artifact.size_bytes,
            "blocks": self.blocks,
        }


class ProviderBackedOcrClient:
    """Calls the configured OCR integration and requires redacted output."""

    def __init__(self, config: OcrProviderConfig):
        self._config = config

    def ocr_region(self, image_path: Path, region: str | None = None) -> OcrProviderResult:
        source_artifact = VisualArtifact.from_file(image_path)
        token = resolve_secret_value(self._config.secret_ref_key)
        if not token:
            raise OcrProviderError("OCR provider secretRef could not be resolved.")

        response_payload = self._request_ocr(image_path, token, region)
        return parse_ocr_response(
            response_payload,
            provider_kind=self._config.provider_kind,
            source_artifact=source_artifact,
        )

    def _request_ocr(
        self,
        image_path: Path,
        token: str,
        region: str | None,
    ) -> Mapping[str, Any]:
        try:
            import httpx
        except ImportError as exc:
            raise OcrProviderError("httpx is required for OCR provider calls.") from exc

        data = {}
        if region:
            data["region"] = region

        with image_path.open("rb") as image_file:
            try:
                response = httpx.post(
                    self._config.endpoint_url,
                    headers={"Authorization": f"Bearer {token}"},
                    data=data,
                    files={"image": (image_path.name, image_file, "application/octet-stream")},
                    timeout=self._config.timeout_seconds,
                )
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise OcrProviderError(f"OCR provider request failed: {exc}") from exc

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise OcrProviderError("OCR provider returned invalid JSON.") from exc

        if not isinstance(payload, Mapping):
            raise OcrProviderError("OCR provider response must be a JSON object.")

        return payload


def parse_ocr_response(
    payload: Mapping[str, Any],
    provider_kind: str,
    source_artifact: VisualArtifact,
) -> OcrProviderResult:
    redacted_text = payload.get("redactedText")
    if not isinstance(redacted_text, str) or not redacted_text.strip():
        raise OcrProviderError("OCR provider response must include redactedText.")

    redaction_applied = payload.get("redactionApplied")
    if not isinstance(redaction_applied, bool):
        raise OcrProviderError("OCR provider response must include redactionApplied.")

    confidence = payload.get("confidence")
    if confidence is not None:
        if not isinstance(confidence, (int, float)):
            raise OcrProviderError("OCR provider confidence must be numeric.")
        confidence = float(confidence)
        if confidence < 0 or confidence > 1:
            raise OcrProviderError("OCR provider confidence must be between 0 and 1.")

    blocks = payload.get("blocks", [])
    if not isinstance(blocks, list) or not all(isinstance(item, dict) for item in blocks):
        raise OcrProviderError("OCR provider blocks must be a list of objects.")

    return OcrProviderResult(
        text=redacted_text,
        confidence=confidence,
        redaction_applied=redaction_applied,
        provider_kind=provider_kind,
        source_artifact=source_artifact,
        blocks=blocks,
    )


def resolve_secret_value(secret_ref_key: str) -> str | None:
    """Resolve a secretRef through the configured runner secrets manager."""

    try:
        from .secrets.manager import get_secrets_manager
    except ImportError as exc:
        raise OcrProviderError("Runner secrets manager is required for OCR.") from exc

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        raise OcrProviderError("OCR secretRef cannot be resolved inside a running event loop.")

    return asyncio.run(get_secrets_manager().get_secret(secret_ref_key))


def _read_positive_float(value: str | None, default: float) -> float:
    if value is None or not value.strip():
        return default

    try:
        parsed = float(value)
    except ValueError as exc:
        raise OcrProviderError("OCR provider timeout must be numeric.") from exc

    if parsed <= 0:
        raise OcrProviderError("OCR provider timeout must be greater than zero.")

    return parsed
