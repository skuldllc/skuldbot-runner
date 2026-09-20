# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Active-run heartbeat projections shared by the runner agent and tests."""

from collections.abc import Mapping
from typing import Any

from .models import ActiveRunState


def active_run_ids(active_jobs: Mapping[str, Any]) -> list[str]:
    """Return every active run id without collapsing concurrent runs."""

    return list(active_jobs)


def primary_active_run_id(active_jobs: Mapping[str, Any]) -> str | None:
    """Return the legacy one-run heartbeat projection, if any."""

    run_ids = active_run_ids(active_jobs)
    return run_ids[0] if run_ids else None


def active_run_states_for_heartbeat(
    active_jobs: Mapping[str, Any],
    active_run_states: Mapping[str, ActiveRunState],
) -> list[ActiveRunState]:
    """Return heartbeat details for every active run."""

    return [
        active_run_states.get(run_id, ActiveRunState(run_id=run_id))
        for run_id in active_run_ids(active_jobs)
    ]
