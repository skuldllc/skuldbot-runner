# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

import asyncio
import hashlib
import json
import os
import shutil
import threading
from datetime import datetime
from email.parser import BytesParser
from email.policy import default as email_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from skuldbot_runner.agent import RunnerAgent
from skuldbot_runner.artifact_uploader import OrchestratorArtifactUploader
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


class _ConcurrentEvidenceUploadingExecutor(_ConcurrentRecordingExecutor):
    def __init__(
        self,
        *,
        expected_jobs: int,
        artifact_root: Path,
        orchestrator_url: str,
    ) -> None:
        super().__init__(expected_jobs)
        self.artifact_root = artifact_root
        self.orchestrator_url = orchestrator_url
        self.uploaded_artifacts: dict[str, dict[str, Any]] = {}

    async def execute(self, *, job, execution_environment=None, **_kwargs):
        self.environments[job.id] = dict(execution_environment or {})
        if len(self.environments) == self.expected_jobs:
            self.all_started.set()
        await asyncio.wait_for(self.release.wait(), timeout=5)

        artifact_dir = self.artifact_root / job.id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / "screen.png"
        payload = f"visual-evidence:{job.id}".encode("utf-8")
        artifact_path.write_bytes(payload)
        checksum = hashlib.sha256(payload).hexdigest()
        uploaded = await asyncio.to_thread(
            OrchestratorArtifactUploader(
                environment={
                    "SKULDBOT_ORCHESTRATOR_URL": self.orchestrator_url,
                    "SKULDBOT_API_KEY": "runner-evidence-key",
                    "SKULDBOT_EVIDENCE_CLASSIFICATION": "confidential",
                    "SKULDBOT_EVIDENCE_UPLOAD_TIMEOUT_SECONDS": "5",
                }
            ).upload,
            run_id=job.id,
            action="screenshot",
            artifact_path=artifact_path,
            checksum_sha256=checksum,
            mime_type="image/png",
            redaction_applied=True,
        )
        self.uploaded_artifacts[job.id] = uploaded.to_robot_dict()
        now = datetime.utcnow()
        return RunResult(
            run_id=job.id,
            status=RunStatus.SUCCEEDED,
            started_at=now,
            completed_at=now,
            duration_ms=1,
            steps_completed=1,
            steps_failed=0,
            output={"uploadedArtifact": uploaded.to_robot_dict()},
            artifacts=[uploaded.artifact_id],
        )


class _RecordingClient:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.completed: list[str] = []
        self.results: list[RunResult] = []

    async def start_run(self, run_id: str) -> None:
        self.started.append(run_id)

    async def complete_run(self, result: RunResult) -> None:
        self.completed.append(result.run_id)
        self.results.append(result)


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


@pytest.mark.asyncio
async def test_execute_job_uploads_concurrent_visual_evidence_without_cross_run_mix(
    tmp_path,
):
    with _artifact_upload_server() as server:
        slots = _windows_slots()
        executor = _ConcurrentEvidenceUploadingExecutor(
            expected_jobs=2,
            artifact_root=tmp_path / "artifacts",
            orchestrator_url=server.base_url,
        )
        agent = _agent_with_executor(executor)
        agent._windows_session_pool = WindowsInteractiveSessionPool(
            slots=slots,
            base_environment={
                "SKULDBOT_WINDOWS_SESSION_BROKER_COMMAND": "skuldbot-win-broker"
            },
        )
        jobs = [
            Job(
                id="run-evidence-a",
                package_url="memory://run-evidence-a.skb",
                display_lease=_display_lease(
                    run_id="run-evidence-a",
                    runtime_plane="windows_interactive",
                    session_id="session-a",
                ),
            ),
            Job(
                id="run-evidence-b",
                package_url="memory://run-evidence-b.skb",
                display_lease=_display_lease(
                    run_id="run-evidence-b",
                    runtime_plane="windows_interactive",
                    session_id="session-b",
                ),
            ),
        ]

        tasks = [asyncio.create_task(agent._execute_job(job)) for job in jobs]
        await asyncio.wait_for(executor.all_started.wait(), timeout=5)

        assert agent._windows_session_pool.active_count == 2
        assert set(agent._active_run_ids()) == {"run-evidence-a", "run-evidence-b"}
        executor.release.set()
        await asyncio.gather(*tasks)

    assert sorted(agent.client.completed) == ["run-evidence-a", "run-evidence-b"]
    assert {result.status for result in agent.client.results} == {RunStatus.SUCCEEDED}
    assert set(server.uploads) == {"run-evidence-a", "run-evidence-b"}
    assert set(executor.uploaded_artifacts) == {"run-evidence-a", "run-evidence-b"}
    assert (
        executor.uploaded_artifacts["run-evidence-a"]["artifactId"]
        != executor.uploaded_artifacts["run-evidence-b"]["artifactId"]
    )
    assert {
        upload["path"] for upload in server.uploads.values()
    } == {
        "/runner-agent/runs/run-evidence-a/artifacts",
        "/runner-agent/runs/run-evidence-b/artifacts",
    }
    assert {
        upload["checksum_ok"] for upload in server.uploads.values()
    } == {True}
    assert {
        executor.environments["run-evidence-a"]["SKULDBOT_WINDOWS_SESSION_ID"],
        executor.environments["run-evidence-b"]["SKULDBOT_WINDOWS_SESSION_ID"],
    } == {"session-a", "session-b"}


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("SKULDBOT_XVFB_INTEGRATION") != "1",
    reason="Set SKULDBOT_XVFB_INTEGRATION=1 to start real isolated Xvfb sessions.",
)
async def test_execute_job_uploads_two_linux_xvfb_artifacts_with_distinct_displays(
    tmp_path,
):
    if shutil.which("Xvfb") is None:
        pytest.skip("Xvfb executable is not available.")

    with _artifact_upload_server() as server:
        executor = _ConcurrentEvidenceUploadingExecutor(
            expected_jobs=2,
            artifact_root=tmp_path / "artifacts",
            orchestrator_url=server.base_url,
        )
        agent = _agent_with_executor(executor)
        agent._linux_virtual_display_pool = LinuxVirtualDisplayPool(
            base_config=LinuxVirtualDisplayConfig(
                display=os.environ.get("SKULDBOT_XVFB_AGENT_TEST_DISPLAY", ":150"),
                startup_timeout_seconds=3.0,
            ),
            max_sessions=2,
            base_environment={},
        )
        jobs = [
            Job(
                id="run-xvfb-evidence-a",
                package_url="memory://run-xvfb-evidence-a.skb",
                display_lease=_display_lease(
                    run_id="run-xvfb-evidence-a",
                    runtime_plane="linux_virtual_display",
                ),
            ),
            Job(
                id="run-xvfb-evidence-b",
                package_url="memory://run-xvfb-evidence-b.skb",
                display_lease=_display_lease(
                    run_id="run-xvfb-evidence-b",
                    runtime_plane="linux_virtual_display",
                ),
            ),
        ]

        tasks = [asyncio.create_task(agent._execute_job(job)) for job in jobs]
        await asyncio.wait_for(executor.all_started.wait(), timeout=5)
        displays = {
            executor.environments["run-xvfb-evidence-a"]["DISPLAY"],
            executor.environments["run-xvfb-evidence-b"]["DISPLAY"],
        }
        assert len(displays) == 2
        executor.release.set()
        await asyncio.gather(*tasks)

    assert set(server.uploads) == {
        "run-xvfb-evidence-a",
        "run-xvfb-evidence-b",
    }
    assert {upload["checksum_ok"] for upload in server.uploads.values()} == {True}


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


class _ArtifactUploadServer:
    def __init__(self, server: ThreadingHTTPServer) -> None:
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.base_url = f"http://127.0.0.1:{server.server_port}"

    @property
    def uploads(self) -> dict[str, dict[str, Any]]:
        return self._server.uploads  # type: ignore[attr-defined]

    def __enter__(self) -> "_ArtifactUploadServer":
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def _artifact_upload_server() -> _ArtifactUploadServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            fields = _parse_multipart_fields(
                self.headers.get("Content-Type", ""),
                body,
            )
            artifact = fields.get("artifact")
            expected_checksum = _as_text(fields.get("expectedChecksumSha256"))
            checksum = hashlib.sha256(artifact or b"").hexdigest()
            run_id = self.path.split("/runs/", 1)[1].split("/artifacts", 1)[0]
            self.server.uploads[run_id] = {  # type: ignore[attr-defined]
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "checksum_ok": checksum == expected_checksum,
                "action": _as_text(fields.get("action")),
                "classification": _as_text(fields.get("classification")),
                "redactionApplied": _as_text(fields.get("redactionApplied")),
                "size": len(artifact or b""),
            }
            if self.headers.get("Authorization") != "Bearer runner-evidence-key":
                self.send_response(401)
                self.end_headers()
                return
            if checksum != expected_checksum:
                self.send_response(400)
                self.end_headers()
                return

            response = {
                "artifactId": f"artifact-{run_id}",
                "action": _as_text(fields.get("action")),
                "checksumSha256": checksum,
                "sizeBytes": len(artifact or b""),
                "mimeType": "image/png",
                "classification": _as_text(fields.get("classification")),
                "redactionApplied": True,
            }
            payload = json.dumps(response).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.uploads = {}  # type: ignore[attr-defined]
    return _ArtifactUploadServer(server)


def _parse_multipart_fields(
    content_type: str,
    body: bytes,
) -> dict[str, bytes]:
    message = BytesParser(policy=email_policy).parsebytes(
        (
            f"Content-Type: {content_type}\r\n"
            "MIME-Version: 1.0\r\n"
            "\r\n"
        ).encode("utf-8")
        + body
    )
    fields: dict[str, bytes] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        fields[name] = part.get_payload(decode=True) or b""
    return fields


def _as_text(value: bytes | None) -> str:
    return (value or b"").decode("utf-8")
