# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

from skuldbot_runner.models import ClaimResponse, DisplayLeaseState, Job


def test_job_parses_current_orchestrator_payload():
    payload = {
        "runId": "run-123",
        "botVersionId": "ver-1",
        "inputs": {"claimId": "A-10"},
        "queuedAt": "2026-02-20T10:00:00Z",
    }

    job = Job.from_api_payload(payload)

    assert job.id == "run-123"
    assert job.bot_version_id == "ver-1"
    assert job.inputs == {"claimId": "A-10"}
    assert job.package_url is None


def test_claim_response_parses_plan_job_payload():
    payload = {
        "success": True,
        "job": {
            "runId": "run-987",
            "botVersionId": "ver-9",
            "inputs": {"foo": "bar"},
            "plan": {
                "entryStepId": "step_0",
                "run": {"botId": "fnol"},
                "steps": [],
                "policy": {"blocks": [], "warnings": []},
            },
        },
    }

    result = ClaimResponse.from_api_payload(payload)

    assert result.success is True
    assert result.job is not None
    assert result.job.id == "run-987"
    assert result.job.bot_name == "fnol"
    assert result.job.plan is not None


def test_job_parses_display_lease_payload():
    payload = {
        "runId": "run-graphical",
        "botVersionId": "ver-visual",
        "displayLease": {
            "leaseId": "lease-1",
            "state": "granted",
            "runnerId": "runner-1",
            "grantedAt": "2026-06-03T10:00:00Z",
            "expiresAt": "2026-06-03T10:10:00Z",
            "request": {
                "leaseRequestId": "lease-request-1",
                "tenantId": "tenant-1",
                "runId": "run-graphical",
                "stepId": "step-1",
                "runtimePlane": "linux_virtual_display",
                "mode": "unattended",
                "requiredCapabilities": ["meditech"],
                "requiredVisualActions": ["screenshot", "type_text"],
                "sessionCredentialRefs": [],
                "reason": "graphical automation",
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
        },
    }

    job = Job.from_api_payload(payload)

    assert job.display_lease is not None
    assert job.display_lease.lease_id == "lease-1"
    assert job.display_lease.state is DisplayLeaseState.GRANTED
    assert job.display_lease.request.required_visual_actions[0].value == "screenshot"
