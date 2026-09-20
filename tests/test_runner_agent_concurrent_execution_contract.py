# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import asyncio
import os
import shutil
from datetime import datetime

import pytest

from skuldbot_runner.agent import RunnerAgent
from skuldbot_runner.config import RunnerConfig
from skuldbot_runner.linux_virtual_display import (
    LinuxVirtualDisplayConfig,
    LinuxVirtualDisplayPool,
)
from skuldbot_runner.models import DisplayLease, Job, RunResult, RunStatus
from skuldbot_runner.windows_session_pool import (
    WindowsInteractiveSessionPool,
    WindowsSessionSlot,
    slots_from_environment,
)


def _display_lease(
    *,
    run_id: str,
    runtime_plane: str,
    session_id: str = "session-concurrent",
) -> DisplayLease:
    return DisplayLease.model_validate(
        {
            "leaseId": f"lease-{run_id}",
            "state": "active",
            "runnerId": "runner-concurrent",
            "grantedAt": "2026-06-03T10:00:00Z",
            "expiresAt": "2026-06-03T10:10:00Z",
            "request": {
                "leaseRequestId": f"lease-request-{run_id}",
                "tenantId": "tenant-concurrent",
                "runId": run_id,
                "stepId": "step-concurrent",
                "runtimePlane": runtime_plane,
                "mode": "unattended",
                "requiredCapabilities": ["graphical_display"],
                "requiredVisualActions": ["screenshot"],
                "sessionCredentialRefs": [],
                "reason": "concurrent visual execution test",
            },
            "session": {
                "sessionId": session_id,
                "tenantId": "tenant-concurrent",
                "runnerId": "runner-concurrent",
                "runtimePlane": runtime_plane,
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


class _ConcurrentRecordingExecutor:
    def __init__(self, expected_jobs: int) -> None:
        self.expected_jobs = expected_jobs
        self.environments: dict[str, dict[str, str]] = {}
        self.all_started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, *, job, execution_environment=None, **_kwargs):
        self.environments[job.id] = dict(execution_environment or {})
        if len(self.environments) == self.expected_jobs:
            self.all_started.set()
        await asyncio.wait_for(self.release.wait(), timeout=5)
        now = datetime.utcnow()
        return RunResult(
            run_id=job.id,
            status=RunStatus.SUCCEEDED,
            started_at=now,
            completed_at=now,
            duration_ms=1,
            steps_completed=1,
            steps_failed=0,
        )


class _RecordingClient:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.completed: list[str] = []

    async def start_run(self, run_id: str) -> None:
        self.started.append(run_id)

    async def complete_run(self, result: RunResult) -> None:
        self.completed.append(result.run_id)


class _ConcurrentTestAgent(RunnerAgent):
    async def _download_package(self, job: Job) -> str:
        return f"/tmp/{job.id}.skb"


def _agent_with_executor(executor: _ConcurrentRecordingExecutor) -> _ConcurrentTestAgent:
    agent = _ConcurrentTestAgent(RunnerConfig(runner_name="concurrent-agent"))
    agent.client = _RecordingClient()
    agent.executor = executor
    return agent


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("SKULDBOT_XVFB_INTEGRATION") != "1",
    reason="Set SKULDBOT_XVFB_INTEGRATION=1 to start real isolated Xvfb sessions.",
)
async def test_execute_job_runs_two_linux_visual_jobs_concurrently_on_distinct_xvfb():
    if shutil.which("Xvfb") is None:
        pytest.skip("Xvfb executable is not available.")

    executor = _ConcurrentRecordingExecutor(expected_jobs=2)
    agent = _agent_with_executor(executor)
    agent._linux_virtual_display_pool = LinuxVirtualDisplayPool(
        base_config=LinuxVirtualDisplayConfig(
            display=os.environ.get("SKULDBOT_XVFB_AGENT_TEST_DISPLAY", ":140"),
            startup_timeout_seconds=3.0,
        ),
        max_sessions=2,
        base_environment={},
    )
    jobs = [
        Job(
            id="run-linux-a",
            package_url="memory://run-linux-a.skb",
            display_lease=_display_lease(
                run_id="run-linux-a",
                runtime_plane="linux_virtual_display",
            ),
        ),
        Job(
            id="run-linux-b",
            package_url="memory://run-linux-b.skb",
            display_lease=_display_lease(
                run_id="run-linux-b",
                runtime_plane="linux_virtual_display",
            ),
        ),
    ]

    tasks = [asyncio.create_task(agent._execute_job(job)) for job in jobs]
    await asyncio.wait_for(executor.all_started.wait(), timeout=5)

    assert agent._linux_virtual_display_pool.active_count == 2
    assert agent._active_run_ids() == ["run-linux-a", "run-linux-b"]
    assert {state.runtime_plane for state in agent._active_run_states_for_heartbeat()} == {
        "linux_virtual_display"
    }
    displays = {
        executor.environments["run-linux-a"]["DISPLAY"],
        executor.environments["run-linux-b"]["DISPLAY"],
    }
    assert len(displays) == 2

    executor.release.set()
    await asyncio.gather(*tasks)

    assert agent._linux_virtual_display_pool.active_count == 0
    assert agent._active_run_ids() == []
    assert sorted(agent.client.completed) == ["run-linux-a", "run-linux-b"]


@pytest.mark.asyncio
async def test_execute_job_runs_two_windows_visual_jobs_with_isolated_session_refs():
    slots = _windows_slots()
    executor = _ConcurrentRecordingExecutor(expected_jobs=2)
    agent = _agent_with_executor(executor)
    agent._windows_session_pool = WindowsInteractiveSessionPool(
        slots=slots,
        base_environment={
            "SKULDBOT_WINDOWS_SESSION_BROKER_COMMAND": "skuldbot-win-broker"
        },
    )
    jobs = [
        Job(
            id="run-windows-a",
            package_url="memory://run-windows-a.skb",
            display_lease=_display_lease(
                run_id="run-windows-a",
                runtime_plane="windows_interactive",
                session_id="session-a",
            ),
        ),
        Job(
            id="run-windows-b",
            package_url="memory://run-windows-b.skb",
            display_lease=_display_lease(
                run_id="run-windows-b",
                runtime_plane="windows_interactive",
                session_id="session-b",
            ),
        ),
    ]

    tasks = [asyncio.create_task(agent._execute_job(job)) for job in jobs]
    await asyncio.wait_for(executor.all_started.wait(), timeout=5)

    assert agent._windows_session_pool.active_count == 2
    assert agent._active_run_ids() == ["run-windows-a", "run-windows-b"]
    states = {
        state.run_id: state.model_dump(by_alias=True, exclude_none=True)
        for state in agent._active_run_states_for_heartbeat()
    }
    assert states["run-windows-a"]["runtimePlane"] == "windows_interactive"
    assert states["run-windows-b"]["runtimePlane"] == "windows_interactive"
    assert states["run-windows-a"]["slotId"] != states["run-windows-b"]["slotId"]

    env_a = executor.environments["run-windows-a"]
    env_b = executor.environments["run-windows-b"]
    assert env_a["SKULDBOT_WINDOWS_SESSION_ID"] != env_b["SKULDBOT_WINDOWS_SESSION_ID"]
    assert env_a["SKULDBOT_WINDOWS_ROBOT_USER_REF"] != env_b[
        "SKULDBOT_WINDOWS_ROBOT_USER_REF"
    ]
    assert env_a["SKULDBOT_WINDOWS_SESSION_PROFILE_REF"] != env_b[
        "SKULDBOT_WINDOWS_SESSION_PROFILE_REF"
    ]
    assert env_a["SKULDBOT_WINDOWS_SESSION_TEMP_ROOT_REF"] != env_b[
        "SKULDBOT_WINDOWS_SESSION_TEMP_ROOT_REF"
    ]
    assert env_a["SKULDBOT_WINDOWS_SESSION_DOWNLOADS_ROOT_REF"] != env_b[
        "SKULDBOT_WINDOWS_SESSION_DOWNLOADS_ROOT_REF"
    ]

    executor.release.set()
    await asyncio.gather(*tasks)

    assert agent._windows_session_pool.active_count == 0
    assert agent._active_run_ids() == []
    assert sorted(agent.client.completed) == ["run-windows-a", "run-windows-b"]


def _windows_slots() -> list[WindowsSessionSlot]:
    return slots_from_environment(
        {
            "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_ENABLED": "true",
            "SKULDBOT_WINDOWS_INTERACTIVE_SESSION_POOL_JSON": """
            [
              {
                "sessionId": "session-a",
                "robotUserRef": "robot-user-a",
                "credentialRefKey": "vault-key-a",
                "isolation": {
                  "kind": "dedicated_user_session",
                  "inputIsolated": true,
                  "clipboardIsolated": true,
                  "profileRef": "profile://session-a",
                  "tempRootRef": "temp://session-a",
                  "downloadsRootRef": "downloads://session-a"
                }
              },
              {
                "sessionId": "session-b",
                "robotUserRef": "robot-user-b",
                "credentialRefKey": "vault-key-b",
                "isolation": {
                  "kind": "dedicated_user_session",
                  "inputIsolated": true,
                  "clipboardIsolated": true,
                  "profileRef": "profile://session-b",
                  "tempRootRef": "temp://session-b",
                  "downloadsRootRef": "downloads://session-b"
                }
              }
            ]
            """,
        }
    )
