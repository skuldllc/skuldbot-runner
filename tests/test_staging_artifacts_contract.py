# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import pytest

from skuldbot_runner.staging_artifacts import (
    STAGING_MANIFEST_ENV,
    STAGING_ROOT_ENV,
    cleanup_uploaded_staging_artifacts,
    record_uploaded_staging_artifact,
    require_evidence_staging_path,
)


def test_cleanup_deletes_only_uploaded_files_inside_evidence_staging_root(tmp_path):
    staging_root = tmp_path / "evidence-staging"
    reference_root = tmp_path / "reference-assets"
    staging_root.mkdir()
    reference_root.mkdir()

    staged = staging_root / "screen.png"
    reference = reference_root / "button.png"
    staged.write_bytes(b"screen")
    reference.write_bytes(b"button")
    manifest = staging_root / "uploaded-artifacts.jsonl"

    record_uploaded_staging_artifact(
        artifact_path=staged,
        artifact_id="artifact-1",
        action="screenshot",
        environment={STAGING_MANIFEST_ENV: str(manifest)},
    )
    record_uploaded_staging_artifact(
        artifact_path=reference,
        artifact_id="artifact-2",
        action="image_click_reference",
        environment={STAGING_MANIFEST_ENV: str(manifest)},
    )

    result = cleanup_uploaded_staging_artifacts(
        manifest_path=manifest,
        allowed_roots=[staging_root],
    )

    assert result.deleted == 1
    assert result.skipped == 1
    assert not staged.exists()
    assert reference.exists()


def test_generated_evidence_must_be_written_under_staging_root(tmp_path):
    staging_root = tmp_path / "evidence-staging"
    reference_root = tmp_path / "reference-assets"
    staging_root.mkdir()
    reference_root.mkdir()

    require_evidence_staging_path(
        staging_root / "screen.png",
        environment={STAGING_ROOT_ENV: str(staging_root)},
    )

    with pytest.raises(ValueError, match="references/assets use separate folders"):
        require_evidence_staging_path(
            reference_root / "button.png",
            environment={STAGING_ROOT_ENV: str(staging_root)},
        )
