# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Local runner capacity decisions made before claiming an orchestrator job."""

from __future__ import annotations

from .linux_virtual_display import LinuxVirtualDisplayPool
from .models import GraphicalRuntimePlane, Job


def job_requires_linux_virtual_display(job: Job) -> bool:
    """Return true when a job requires the Linux virtual display plane."""

    return (
        job.display_lease is not None
        and job.display_lease.request.runtime_plane
        == GraphicalRuntimePlane.LINUX_VIRTUAL_DISPLAY
    )


def runner_can_claim_job_locally(
    job: Job,
    *,
    active_jobs: int,
    max_concurrent_jobs: int,
    linux_virtual_display_pool: LinuxVirtualDisplayPool | None,
    reserved_linux_virtual_display_slots: int = 0,
) -> bool:
    """Return true only when this runner has local capacity before claiming."""

    if active_jobs >= max_concurrent_jobs:
        return False

    if not job_requires_linux_virtual_display(job):
        return True

    if linux_virtual_display_pool is None:
        return False

    projected_graphical_sessions = (
        linux_virtual_display_pool.active_count + reserved_linux_virtual_display_slots
    )
    return projected_graphical_sessions < linux_virtual_display_pool.max_sessions
