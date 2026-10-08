# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Orchestrator API URL contract helpers."""

from urllib.parse import urlparse

REQUIRED_API_PREFIX = "/api/v1"


def require_orchestrator_api_v1_url(value: str) -> str:
    """Return a normalized Orchestrator API URL, failing closed unless it is versioned.

    The runner stores and uses the API base URL itself. During the no-alias
    `/api` -> `/api/v1` cutover it must not silently append or translate an
    unversioned host, because installed runners would appear healthy while
    still depending on the retired contract.
    """

    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Orchestrator URL must be an absolute http(s) URL ending in /api/v1.")

    path = parsed.path.rstrip("/")
    if path != REQUIRED_API_PREFIX:
        raise ValueError(
            "Orchestrator URL must end in /api/v1. No /api compatibility alias exists."
        )

    return normalized
