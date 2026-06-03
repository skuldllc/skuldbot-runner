# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import os

from skuldbot_runner.executor import display_lease_environment
from skuldbot_runner.graphical_runtime import read_display_lease_context
from skuldbot_runner.models import DisplayLease, Job


def _display_lease() -> DisplayLease:
    return DisplayLease.model_validate(
        {
            "leaseId": "lease-1",
            "state": "active",
            "runnerId": "runner-1",
            "grantedAt": "2026-06-03T10:00:00Z",
            "expiresAt": "2026-06-03T10:10:00Z",
            "request": {
                "leaseRequestId": "lease-request-1",
                "tenantId": "tenant-1",
                "runId": "run-1",
                "stepId": "step-1",
                "runtimePlane": "linux_virtual_display",
                "mode": "unattended",
                "requiredCapabilities": ["meditech"],
                "requiredVisualActions": ["screenshot", "type_text"],
                "sessionCredentialRefs": [],
                "reason": "visual runtime",
            },
            "session": {
                "sessionId": "session-1",
                "tenantId": "tenant-1",
                "runnerId": "runner-1",
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


def test_executor_display_lease_environment_is_scoped():
    job = Job(
        id="run-1",
        display_lease=_display_lease(),
    )

    assert read_display_lease_context({}) is None
    assert os.environ.get("SKULDBOT_DISPLAY_LEASE_ID") is None

    with display_lease_environment(job):
        context = read_display_lease_context()
        assert context is not None
        assert context.lease_id == "lease-1"
        assert context.run_id == "run-1"

    assert os.environ.get("SKULDBOT_DISPLAY_LEASE_ID") is None
