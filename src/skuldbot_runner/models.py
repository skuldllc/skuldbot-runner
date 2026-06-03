# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Data models for runner communication with Orchestrator."""

from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(str, Enum):
    """Status of a run."""

    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_success(self) -> bool:
        return self in {RunStatus.SUCCESS, RunStatus.SUCCEEDED}


class StepStatus(str, Enum):
    """Status of a step within a run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class LogLevel(str, Enum):
    """Log level for streaming logs."""

    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class GraphicalRuntimePlane(str, Enum):
    """Graphical runtime planes supported by the Orchestrator contract."""

    WINDOWS_INTERACTIVE = "windows_interactive"
    CITRIX_PUBLISHED_APP = "citrix_published_app"
    REMOTE_DESKTOP = "remote_desktop"
    LINUX_VIRTUAL_DISPLAY = "linux_virtual_display"


class GraphicalSessionMode(str, Enum):
    """Session modes supported by the graphical runtime contract."""

    ATTENDED = "attended"
    UNATTENDED = "unattended"


class GraphicalDisplayState(str, Enum):
    """Display state values accepted by the Orchestrator contract."""

    AVAILABLE = "available"
    ACTIVE = "active"
    LOCKED = "locked"
    DISCONNECTED = "disconnected"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class DisplayLeaseState(str, Enum):
    """Display lease states accepted by the Orchestrator contract."""

    REQUESTED = "requested"
    GRANTED = "granted"
    ACTIVE = "active"
    HITL_HOLD = "hitl_hold"
    RELEASED = "released"
    EXPIRED = "expired"
    FAILED = "failed"


class VisualActionKind(str, Enum):
    """Visual action kinds supported by graphical runner capabilities."""

    SCREENSHOT = "screenshot"
    WAIT_IMAGE = "wait_image"
    IMAGE_CLICK = "image_click"
    OCR_REGION = "ocr_region"
    ASSERT_TEXT = "assert_text"
    TYPE_TEXT = "type_text"
    HOTKEY = "hotkey"


class DisplayResolution(BaseModel):
    """Display resolution reported to Orchestrator."""

    width: int
    height: int


class GraphicalDisplayStateInfo(BaseModel):
    """Current display state reported by the runner."""

    model_config = ConfigDict(populate_by_name=True)

    state: GraphicalDisplayState
    locked: bool
    connected: bool
    resolution: DisplayResolution
    dpi_scale: float = Field(alias="dpiScale", serialization_alias="dpiScale")
    active_window_title: str | None = Field(
        default=None,
        serialization_alias="activeWindowTitle",
    )
    last_frame_at: str | None = Field(
        default=None,
        alias="lastFrameAt",
        serialization_alias="lastFrameAt",
    )
    stale_after_seconds: int = Field(
        alias="staleAfterSeconds",
        serialization_alias="staleAfterSeconds",
    )


class GraphicalRunnerCapabilities(BaseModel):
    """Explicit graphical capability declaration for fail-closed routing."""

    model_config = ConfigDict(populate_by_name=True)

    has_display: bool = Field(serialization_alias="hasDisplay")
    display: GraphicalDisplayStateInfo
    supported_runtime_planes: list[GraphicalRuntimePlane] = Field(
        serialization_alias="supportedRuntimePlanes",
    )
    supported_session_modes: list[GraphicalSessionMode] = Field(
        serialization_alias="supportedSessionModes",
    )
    supported_visual_actions: list[VisualActionKind] = Field(
        serialization_alias="supportedVisualActions",
    )
    max_graphical_sessions: int = Field(serialization_alias="maxGraphicalSessions")
    current_graphical_sessions: int = Field(
        serialization_alias="currentGraphicalSessions",
    )
    installed_systems: list[str] = Field(serialization_alias="installedSystems")


class SessionCredentialRef(BaseModel):
    """Reference to a session credential resolved by the Orchestrator."""

    model_config = ConfigDict(populate_by_name=True)

    secret_ref_key: str = Field(alias="secretRefKey", serialization_alias="secretRefKey")
    required_at_step: str = Field(
        alias="requiredAtStep",
        serialization_alias="requiredAtStep",
    )
    data_classes: list[str] = Field(
        default_factory=list,
        alias="dataClasses",
        serialization_alias="dataClasses",
    )


class GraphicalSession(BaseModel):
    """Graphical session granted by the Orchestrator."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId", serialization_alias="sessionId")
    tenant_id: str = Field(alias="tenantId", serialization_alias="tenantId")
    runner_id: str = Field(alias="runnerId", serialization_alias="runnerId")
    runtime_plane: GraphicalRuntimePlane = Field(
        alias="runtimePlane",
        serialization_alias="runtimePlane",
    )
    mode: GraphicalSessionMode
    display: GraphicalDisplayStateInfo
    acquired_at: str = Field(alias="acquiredAt", serialization_alias="acquiredAt")
    expires_at: str | None = Field(
        default=None,
        alias="expiresAt",
        serialization_alias="expiresAt",
    )


class DisplayLeaseRequest(BaseModel):
    """Display lease request embedded in a granted display lease."""

    model_config = ConfigDict(populate_by_name=True)

    lease_request_id: str = Field(
        alias="leaseRequestId",
        serialization_alias="leaseRequestId",
    )
    tenant_id: str = Field(alias="tenantId", serialization_alias="tenantId")
    run_id: str = Field(alias="runId", serialization_alias="runId")
    step_id: str = Field(alias="stepId", serialization_alias="stepId")
    runtime_plane: GraphicalRuntimePlane = Field(
        alias="runtimePlane",
        serialization_alias="runtimePlane",
    )
    mode: GraphicalSessionMode
    required_capabilities: list[str] = Field(
        default_factory=list,
        alias="requiredCapabilities",
        serialization_alias="requiredCapabilities",
    )
    required_visual_actions: list[VisualActionKind] = Field(
        default_factory=list,
        alias="requiredVisualActions",
        serialization_alias="requiredVisualActions",
    )
    session_credential_refs: list[SessionCredentialRef] = Field(
        default_factory=list,
        alias="sessionCredentialRefs",
        serialization_alias="sessionCredentialRefs",
    )
    reason: str


class DisplayLease(BaseModel):
    """Display lease granted by Orchestrator and required for visual actions."""

    model_config = ConfigDict(populate_by_name=True)

    lease_id: str = Field(alias="leaseId", serialization_alias="leaseId")
    request: DisplayLeaseRequest
    state: DisplayLeaseState
    runner_id: str = Field(alias="runnerId", serialization_alias="runnerId")
    session: GraphicalSession
    granted_at: str = Field(alias="grantedAt", serialization_alias="grantedAt")
    expires_at: str = Field(alias="expiresAt", serialization_alias="expiresAt")


# ============================================
# Registration
# ============================================


class SystemInfo(BaseModel):
    """System information about the runner machine."""

    hostname: str
    os: str
    os_version: str
    python_version: str
    cpu_count: int
    memory_total_mb: int
    memory_available_mb: int


class RegisterRequest(BaseModel):
    """Request to register a new runner."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    labels: dict[str, str] = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)
    system_info: SystemInfo = Field(serialization_alias="systemInfo")
    graphical_capabilities: GraphicalRunnerCapabilities | None = Field(
        default=None,
        serialization_alias="graphicalCapabilities",
    )


class RegisterResponse(BaseModel):
    """Response from runner registration."""

    id: str
    api_key: str
    name: str
    tenant_id: str

    @classmethod
    def from_api_payload(cls, payload: Mapping[str, Any]) -> "RegisterResponse":
        """
        Supports both formats:
        - Legacy: {id, api_key, tenant_id, ...}
        - Current orchestrator: {runner: {id, tenantId, ...}, apiKey}
        """
        runner_payload = payload.get("runner")
        if isinstance(runner_payload, Mapping):
            return cls(
                id=str(runner_payload.get("id", "")),
                api_key=str(payload.get("apiKey", payload.get("api_key", ""))),
                name=str(runner_payload.get("name", "")),
                tenant_id=str(
                    runner_payload.get("tenantId", runner_payload.get("tenant_id", ""))
                ),
            )

        return cls(
            id=str(payload.get("id", "")),
            api_key=str(payload.get("apiKey", payload.get("api_key", ""))),
            name=str(payload.get("name", "")),
            tenant_id=str(payload.get("tenantId", payload.get("tenant_id", ""))),
        )


# ============================================
# Heartbeat
# ============================================


class HeartbeatRequest(BaseModel):
    """Heartbeat request with current status."""

    model_config = ConfigDict(populate_by_name=True)

    status: str = "online"  # online, busy, offline
    current_run_id: str | None = None
    system_info: SystemInfo | None = None
    graphical_capabilities: GraphicalRunnerCapabilities | None = Field(
        default=None,
        serialization_alias="graphicalCapabilities",
    )


class HeartbeatResponse(BaseModel):
    """Heartbeat response."""

    acknowledged: bool
    pending_jobs: int | None = None
    server_time: datetime | None = None

    @classmethod
    def from_api_payload(cls, payload: Mapping[str, Any]) -> "HeartbeatResponse":
        return cls(
            acknowledged=bool(payload.get("acknowledged", False)),
            pending_jobs=(
                int(payload["pendingJobs"])
                if payload.get("pendingJobs") is not None
                else None
            ),
            server_time=payload.get("serverTime", payload.get("server_time")),
        )


# ============================================
# Jobs
# ============================================


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.utcnow()
    return datetime.utcnow()


class Job(BaseModel):
    """A job (run) to be executed."""

    id: str
    bot_id: str = ""
    bot_version_id: str = ""
    bot_name: str = "unknown-bot"
    package_url: str | None = None
    plan: dict[str, Any] | None = None
    display_lease: DisplayLease | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    @classmethod
    def from_api_payload(cls, payload: Mapping[str, Any]) -> "Job":
        """
        Supports both formats:
        - Legacy runner: {id, bot_name, package_url, ...}
        - Current orchestrator: {runId, botVersionId, inputs, queuedAt} or claim payload with plan
        """
        run_id = payload.get("id") or payload.get("runId")
        if not run_id:
            raise ValueError("Invalid job payload: missing id/runId")

        plan = payload.get("plan")
        run_meta = plan.get("run", {}) if isinstance(plan, Mapping) else {}

        created_at = (
            payload.get("created_at")
            or payload.get("createdAt")
            or payload.get("queuedAt")
        )
        inputs = payload.get("inputs", {})
        if not isinstance(inputs, Mapping):
            inputs = {}
        raw_display_lease = payload.get("displayLease") or payload.get("display_lease")

        bot_id = (
            payload.get("bot_id")
            or payload.get("botId")
            or run_meta.get("botId")
            or ""
        )
        bot_version_id = (
            payload.get("bot_version_id")
            or payload.get("botVersionId")
            or run_meta.get("botVersion")
            or ""
        )
        bot_name = (
            payload.get("bot_name")
            or payload.get("botName")
            or payload.get("name")
            or bot_id
            or "unknown-bot"
        )

        return cls(
            id=str(run_id),
            bot_id=str(bot_id),
            bot_version_id=str(bot_version_id),
            bot_name=str(bot_name),
            package_url=(
                payload.get("package_url")
                or payload.get("packageUrl")
                or payload.get("botPackageUrl")
            ),
            plan=plan if isinstance(plan, Mapping) else None,
            display_lease=(
                DisplayLease.model_validate(raw_display_lease)
                if isinstance(raw_display_lease, Mapping)
                else None
            ),
            inputs=dict(inputs),
            created_at=_parse_datetime(created_at),
        )


class ClaimResponse(BaseModel):
    """Response from claiming a job."""

    success: bool
    job: Job | None = None
    message: str | None = None

    @classmethod
    def from_api_payload(cls, payload: Mapping[str, Any]) -> "ClaimResponse":
        raw_job = payload.get("job")
        job = Job.from_api_payload(raw_job) if isinstance(raw_job, Mapping) else None
        return cls(
            success=bool(payload.get("success", False)),
            job=job,
            message=payload.get("message"),
        )


# ============================================
# Progress Reporting
# ============================================


class StepProgress(BaseModel):
    """Progress report for a single step."""

    step_id: str
    node_id: str
    node_type: str
    status: StepStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    output: Any | None = None
    error: str | None = None
    run_id: str | None = None


class ProgressReport(BaseModel):
    """Progress report for a run."""

    run_id: str
    status: RunStatus
    current_step: int | None = None
    total_steps: int | None = None
    steps: list[StepProgress] = Field(default_factory=list)
    logs: list[str] = Field(default_factory=list)


class LogEntry(BaseModel):
    """A single log entry for streaming."""

    run_id: str
    timestamp: datetime
    level: LogLevel
    message: str
    node_id: str | None = None
    step_index: int | None = None


# ============================================
# Completion
# ============================================


class RunResult(BaseModel):
    """Final result of a run."""

    run_id: str
    status: RunStatus
    started_at: datetime
    completed_at: datetime
    duration_ms: int
    steps_completed: int
    steps_failed: int
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    logs: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)  # Paths to artifact files
