# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Pure payload builders for Orchestrator API contracts."""

from typing import Any

from .models import HeartbeatRequest


def build_heartbeat_payload(request: HeartbeatRequest) -> dict[str, Any]:
    """Build the Orchestrator heartbeat payload from the runner contract model."""

    payload: dict[str, Any] = {"status": request.status}
    if request.current_run_id:
        payload["currentRunId"] = request.current_run_id

    if request.system_info:
        payload["metrics"] = {
            "cpuPercent": 0,
            "memoryPercent": 0,
            "activeSteps": 0,
        }

    if request.graphical_capabilities:
        payload["graphicalCapabilities"] = request.graphical_capabilities.model_dump(
            by_alias=True,
            exclude_none=True,
        )

    return payload
