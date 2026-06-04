# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

from skuldbot_runner.linux_virtual_display import (
    LinuxVirtualDisplayConfig,
    LinuxVirtualDisplayPool,
)
from skuldbot_runner.models import DisplayLease, Job
from skuldbot_runner.runner_capacity import (
    job_requires_linux_virtual_display,
    runner_can_claim_job_locally,
)


def _display_lease() -> DisplayLease:
    return DisplayLease.model_validate(
        {
            "leaseId": "lease-capacity-1",
            "state": "active",
            "runnerId": "runner-capacity-1",
            "grantedAt": "2026-06-03T10:00:00Z",
            "expiresAt": "2026-06-03T10:10:00Z",
            "request": {
                "leaseRequestId": "lease-request-capacity-1",
                "tenantId": "tenant-capacity-1",
                "runId": "run-capacity-1",
                "stepId": "step-capacity-1",
                "runtimePlane": "linux_virtual_display",
                "mode": "unattended",
                "requiredCapabilities": ["graphical_display"],
                "requiredVisualActions": ["screenshot"],
                "sessionCredentialRefs": [],
                "reason": "capacity contract",
            },
            "session": {
                "sessionId": "session-capacity-1",
                "tenantId": "tenant-capacity-1",
                "runnerId": "runner-capacity-1",
                "runtimePlane": "linux_virtual_display",
                "mode": "unattended",
                "acquiredAt": "2026-06-03T10:00:00Z",
                "display": {
                    "state": "active",
                    "locked": False,
                    "connected": True,
                    "resolution": {"width": 1280, "height": 720},
                    "dpiScale": 1.0,
                    "staleAfterSeconds": 30,
                },
            },
        }
    )


def _pool(max_sessions: int) -> LinuxVirtualDisplayPool:
    return LinuxVirtualDisplayPool(
        base_config=LinuxVirtualDisplayConfig(display=":120"),
        max_sessions=max_sessions,
        base_environment={},
    )


def test_runner_claim_capacity_allows_non_graphical_jobs_without_display_pool():
    job = Job(id="run-api")

    assert (
        runner_can_claim_job_locally(
            job,
            active_jobs=0,
            max_concurrent_jobs=1,
            linux_virtual_display_pool=None,
        )
        is True
    )
    assert job_requires_linux_virtual_display(job) is False


def test_runner_claim_capacity_rejects_graphical_job_without_display_pool():
    job = Job(id="run-graphical", display_lease=_display_lease())

    assert (
        runner_can_claim_job_locally(
            job,
            active_jobs=0,
            max_concurrent_jobs=2,
            linux_virtual_display_pool=None,
        )
        is False
    )
    assert job_requires_linux_virtual_display(job) is True


def test_runner_claim_capacity_reserves_graphical_slots_before_claiming():
    job = Job(id="run-graphical", display_lease=_display_lease())
    pool = _pool(max_sessions=2)

    assert (
        runner_can_claim_job_locally(
            job,
            active_jobs=0,
            max_concurrent_jobs=3,
            linux_virtual_display_pool=pool,
            reserved_linux_virtual_display_slots=0,
        )
        is True
    )
    assert (
        runner_can_claim_job_locally(
            job,
            active_jobs=1,
            max_concurrent_jobs=3,
            linux_virtual_display_pool=pool,
            reserved_linux_virtual_display_slots=1,
        )
        is True
    )
    assert (
        runner_can_claim_job_locally(
            job,
            active_jobs=2,
            max_concurrent_jobs=3,
            linux_virtual_display_pool=pool,
            reserved_linux_virtual_display_slots=2,
        )
        is False
    )


def test_runner_claim_capacity_rejects_when_global_job_capacity_is_full():
    job = Job(id="run-graphical", display_lease=_display_lease())

    assert (
        runner_can_claim_job_locally(
            job,
            active_jobs=2,
            max_concurrent_jobs=2,
            linux_virtual_display_pool=_pool(max_sessions=3),
        )
        is False
    )
