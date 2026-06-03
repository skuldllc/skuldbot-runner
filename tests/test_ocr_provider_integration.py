# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import os
from pathlib import Path

import pytest

from skuldbot_runner.ocr_provider import OcrProviderConfig, ProviderBackedOcrClient

RUN_OCR_TESTS = os.environ.get("SKULDBOT_OCR_PROVIDER_INTEGRATION") == "1"


@pytest.mark.skipif(
    not RUN_OCR_TESTS,
    reason="Set SKULDBOT_OCR_PROVIDER_INTEGRATION=1 with a real OCR provider.",
)
def test_provider_backed_ocr_returns_redacted_text():
    image_path = Path(os.environ["SKULDBOT_OCR_PROVIDER_TEST_IMAGE"])
    assert image_path.is_file()

    result = ProviderBackedOcrClient(
        OcrProviderConfig.from_environment()
    ).ocr_region(image_path)

    assert result.text.strip()
    assert result.redaction_applied is True
    assert result.source_artifact.size_bytes > 0
    assert len(result.source_artifact.checksum_sha256) == 64
