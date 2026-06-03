# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import pytest

from skuldbot_runner.ocr_provider import (
    OcrProviderConfig,
    OcrProviderError,
    parse_ocr_response,
)
from skuldbot_runner.visual_adapter import VisualArtifact


def _artifact(tmp_path):
    image_path = tmp_path / "screen.png"
    image_path.write_bytes(b"image")
    return VisualArtifact.from_file(image_path)


def test_ocr_provider_config_requires_endpoint_and_secret_ref():
    with pytest.raises(OcrProviderError, match="endpoint"):
        OcrProviderConfig.from_environment({})

    with pytest.raises(OcrProviderError, match="secretRef"):
        OcrProviderConfig.from_environment(
            {"SKULDBOT_OCR_PROVIDER_ENDPOINT": "https://ocr.internal/extract"}
        )


def test_ocr_provider_config_rejects_unsupported_provider_kind():
    with pytest.raises(OcrProviderError, match="not supported"):
        OcrProviderConfig.from_environment(
            {
                "SKULDBOT_OCR_PROVIDER_KIND": "vendor_specific_ocr",
                "SKULDBOT_OCR_PROVIDER_ENDPOINT": "https://ocr.internal/extract",
                "SKULDBOT_OCR_PROVIDER_SECRET_REF": "ocr-token",
            }
        )


def test_ocr_response_requires_redacted_text(tmp_path):
    with pytest.raises(OcrProviderError, match="redactedText"):
        parse_ocr_response(
            {"text": "raw patient value", "redactionApplied": True},
            provider_kind="generic_http",
            source_artifact=_artifact(tmp_path),
        )


def test_ocr_response_requires_redaction_flag(tmp_path):
    with pytest.raises(OcrProviderError, match="redactionApplied"):
        parse_ocr_response(
            {"redactedText": "[REDACTED]"},
            provider_kind="generic_http",
            source_artifact=_artifact(tmp_path),
        )


def test_ocr_response_returns_safe_robot_payload(tmp_path):
    result = parse_ocr_response(
        {
            "text": "raw value must be ignored",
            "redactedText": "Policy [REDACTED]",
            "redactionApplied": True,
            "confidence": 0.92,
            "blocks": [{"label": "policy_number", "text": "[REDACTED]"}],
        },
        provider_kind="generic_http",
        source_artifact=_artifact(tmp_path),
    )

    payload = result.to_robot_dict()

    assert payload["text"] == "Policy [REDACTED]"
    assert "raw value" not in str(payload)
    assert payload["redactionApplied"] is True
    assert payload["confidence"] == 0.92
    assert payload["providerKind"] == "generic_http"
    assert payload["sizeBytes"] == 5
    assert len(payload["checksumSha256"]) == 64
