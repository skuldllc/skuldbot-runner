# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Cleanup of local staging files after provider-backed evidence upload."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

STAGING_MANIFEST_ENV = "SKULDBOT_EVIDENCE_STAGING_MANIFEST"
STAGING_ROOT_ENV = "SKULDBOT_EVIDENCE_STAGING_ROOT"


@dataclass(frozen=True)
class StagingCleanupResult:
    """Summary of local staging cleanup."""

    deleted: int
    skipped: int


def record_uploaded_staging_artifact(
    *,
    artifact_path: Path,
    artifact_id: str,
    action: str,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Record a local staging file that can be deleted after the run."""

    env = environment if environment is not None else os.environ
    manifest = env.get(STAGING_MANIFEST_ENV, "").strip()
    if not manifest:
        return

    manifest_path = Path(manifest).expanduser()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "path": str(artifact_path.expanduser()),
        "artifactId": artifact_id,
        "action": action,
    }
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, separators=(",", ":")) + "\n")


def require_evidence_staging_path(
    artifact_path: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Fail closed unless a generated artifact is inside the staging root."""

    env = environment if environment is not None else os.environ
    root = env.get(STAGING_ROOT_ENV, "").strip()
    if not root:
        raise ValueError(f"{STAGING_ROOT_ENV} is required for evidence staging.")

    root_path = Path(root).expanduser()
    if not is_path_inside_root(artifact_path.expanduser(), root_path):
        raise ValueError(
            "Generated evidence artifacts must be written under "
            f"{STAGING_ROOT_ENV}; references/assets use separate folders."
        )


def cleanup_uploaded_staging_artifacts(
    *,
    manifest_path: Path,
    allowed_roots: list[Path],
) -> StagingCleanupResult:
    """Delete uploaded staging files if they are inside allowed roots."""

    if not manifest_path.exists():
        return StagingCleanupResult(deleted=0, skipped=0)

    deleted = 0
    skipped = 0
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        candidate = Path(str(payload.get("path", ""))).expanduser()
        if _delete_if_safe(candidate, allowed_roots):
            deleted += 1
        else:
            skipped += 1

    return StagingCleanupResult(deleted=deleted, skipped=skipped)


def _delete_if_safe(candidate: Path, allowed_roots: list[Path]) -> bool:
    if not candidate.exists() or not candidate.is_file() or candidate.is_symlink():
        return False

    try:
        resolved_candidate = candidate.resolve(strict=True)
        resolved_roots = [root.resolve(strict=False) for root in allowed_roots]
    except OSError:
        return False

    if not any(_is_relative_to(resolved_candidate, root) for root in resolved_roots):
        return False

    candidate.unlink()
    return True


def is_path_inside_root(path: Path, root: Path) -> bool:
    """Return whether path is contained by root after path normalization."""

    try:
        return _is_relative_to(path.resolve(strict=False), root.resolve(strict=False))
    except OSError:
        return False


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
