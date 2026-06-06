# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Main runner agent - the "dumb" worker that polls and executes."""

import asyncio
import os
import platform
import signal
import tempfile
from datetime import datetime
from pathlib import Path

import structlog

from .api_client import OrchestratorClient
from .config import RunnerConfig
from .executor import BotExecutor
from .graphical_runtime import GraphicalProbeInput, build_graphical_capabilities
from .linux_virtual_display import (
    LinuxVirtualDisplayPool,
    config_from_environment,
    should_start_linux_virtual_display,
)
from .models import (
    HeartbeatRequest,
    Job,
    LogEntry,
    ProgressReport,
    RegisterRequest,
    RunResult,
    RunStatus,
    StepProgress,
)
from .runner_capacity import (
    job_requires_linux_virtual_display,
    job_requires_windows_interactive,
    runner_can_claim_job_locally,
)
from .system_info import get_system_info

logger = structlog.get_logger()


class RunnerAgent:
    """
    The "dumb" runner agent.

    It only does:
    1. Register with Orchestrator
    2. Send heartbeats
    3. Poll for jobs
    4. Download bot packages
    5. Execute with Robot Framework
    6. Report results

    All intelligence is in the Orchestrator.
    """

    def __init__(self, config: RunnerConfig):
        self.config = config
        self.client = OrchestratorClient(config)
        self.executor = BotExecutor(config)

        self.runner_id: str | None = None
        self.running = False
        self.current_job: Job | None = None
        self._active_jobs: dict[str, Job] = {}
        self._job_tasks: set[asyncio.Task[None]] = set()
        self._linux_virtual_display_pool: LinuxVirtualDisplayPool | None = None

    async def start(self):
        """Start the runner agent."""
        logger.info("Starting runner agent", name=self.config.runner_name)

        # Setup signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, self._shutdown)

        self.running = True

        try:
            self._configure_linux_virtual_display_pool_if_requested()

            # Register if we don't have an API key
            if not self.config.api_key:
                await self._register()

            # Start background tasks
            heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            poll_task = asyncio.create_task(self._poll_loop())

            # Wait for shutdown
            await asyncio.gather(heartbeat_task, poll_task)

        except asyncio.CancelledError:
            logger.info("Runner agent cancelled")
        except Exception as e:
            logger.exception("Runner agent error", error=str(e))
        finally:
            await self._cleanup()

    def _shutdown(self):
        """Handle shutdown signal."""
        logger.info("Shutdown signal received")
        self.running = False

    async def _cleanup(self):
        """Cleanup resources."""
        if self._job_tasks:
            await asyncio.gather(*self._job_tasks, return_exceptions=True)
        if self._linux_virtual_display_pool is not None:
            self._linux_virtual_display_pool.stop_all()
            self._linux_virtual_display_pool = None
        await self.client.close()
        logger.info("Runner agent stopped")

    def _configure_linux_virtual_display_pool_if_requested(self) -> None:
        """Prepare isolated Xvfb sessions when linux_virtual_display is enabled."""

        if not should_start_linux_virtual_display():
            return

        self._linux_virtual_display_pool = LinuxVirtualDisplayPool(
            base_config=config_from_environment(),
            max_sessions=self.config.max_graphical_sessions,
        )
        logger.info(
            "Linux virtual display pool configured",
            max_sessions=self.config.max_graphical_sessions,
        )

    async def _register(self):
        """Register this runner with the Orchestrator."""
        system_info = get_system_info()

        request = RegisterRequest(
            name=self.config.runner_name or f"runner-{system_info.hostname}",
            labels=self.config.labels,
            capabilities=self.config.capabilities,
            system_info=system_info,
            graphical_capabilities=self._detect_graphical_capabilities(),
        )

        response = await self.client.register(request)

        self.runner_id = response.id
        # Update config with received API key
        self.config.api_key = response.api_key

        # Recreate client with new API key
        await self.client.close()
        self.client = OrchestratorClient(self.config)

        logger.info(
            "Runner registered",
            runner_id=self.runner_id,
            tenant_id=response.tenant_id,
        )

    async def _heartbeat_loop(self):
        """Send periodic heartbeats to Orchestrator."""
        while self.running:
            try:
                system_info = get_system_info()

                request = HeartbeatRequest(
                    status="busy" if self._active_jobs else "online",
                    current_run_id=next(iter(self._active_jobs), None),
                    system_info=system_info,
                    graphical_capabilities=self._detect_graphical_capabilities(),
                )

                await self.client.heartbeat(request)
                logger.debug("Heartbeat sent")

            except Exception as e:
                logger.warning("Heartbeat failed", error=str(e))

            await asyncio.sleep(self.config.heartbeat_interval_seconds)

    async def _poll_loop(self):
        """Poll for jobs and execute them."""
        while self.running:
            try:
                if len(self._active_jobs) < self.config.max_concurrent_jobs:
                    await self._check_for_jobs()

            except Exception as e:
                logger.warning("Poll loop error", error=str(e))

            await asyncio.sleep(self.config.poll_interval_seconds)

    async def _check_for_jobs(self):
        """Check for available jobs and execute one."""
        # Get pending jobs
        jobs = await self.client.get_pending_jobs()

        if not jobs:
            return

        logger.info("Found pending jobs", count=len(jobs))

        # Claim up to the remaining local capacity. Orchestrator still owns final routing.
        remaining_capacity = self.config.max_concurrent_jobs - len(self._active_jobs)
        active_graphical_jobs = sum(
            1
            for active_job in self._active_jobs.values()
            if job_requires_linux_virtual_display(active_job)
        )
        active_graphical_sessions = (
            self._linux_virtual_display_pool.active_count
            if self._linux_virtual_display_pool is not None
            else 0
        )
        reserved_linux_virtual_display_slots = max(
            active_graphical_jobs - active_graphical_sessions,
            0,
        )
        reserved_windows_interactive_slots = 0
        for job in jobs:
            if remaining_capacity <= 0:
                break

            active_jobs_for_capacity = (
                self.config.max_concurrent_jobs - remaining_capacity
            )
            if not runner_can_claim_job_locally(
                job,
                active_jobs=active_jobs_for_capacity,
                max_concurrent_jobs=self.config.max_concurrent_jobs,
                linux_virtual_display_pool=self._linux_virtual_display_pool,
                reserved_linux_virtual_display_slots=reserved_linux_virtual_display_slots,
                windows_interactive_slots_available=0,
                reserved_windows_interactive_slots=reserved_windows_interactive_slots,
            ):
                logger.debug(
                    "Skipping job until local runner capacity is available",
                    job_id=job.id,
                    requires_linux_virtual_display=job_requires_linux_virtual_display(job),
                    requires_windows_interactive=job_requires_windows_interactive(job),
                )
                continue

            claim_response = await self.client.claim_job(job.id)

            if claim_response.success and claim_response.job:
                claimed_job = claim_response.job
                task = asyncio.create_task(self._execute_job(claimed_job))
                self._job_tasks.add(task)
                task.add_done_callback(self._job_tasks.discard)
                remaining_capacity -= 1
                if job_requires_linux_virtual_display(claimed_job):
                    reserved_linux_virtual_display_slots += 1
                if job_requires_windows_interactive(claimed_job):
                    reserved_windows_interactive_slots += 1
            else:
                logger.debug(
                    "Failed to claim job",
                    job_id=job.id,
                    message=claim_response.message,
                )

    async def _execute_job(self, job: Job):
        """Execute a claimed job."""
        self._active_jobs[job.id] = job
        self.current_job = next(iter(self._active_jobs.values()), None)
        logger.info("Executing job", run_id=job.id, bot_name=job.bot_name)

        try:
            # Report that we're starting
            await self.client.start_run(job.id)

            # Download bot package
            package_path = await self._download_package(job)

            # Execute with real-time log streaming
            if self._requires_linux_virtual_display(job):
                if self._linux_virtual_display_pool is None:
                    raise RuntimeError(
                        "Run requires linux_virtual_display but no display pool is configured."
                    )
                with self._linux_virtual_display_pool.acquire(job.id) as display_lease:
                    result = await self.executor.execute(
                        job=job,
                        package_path=package_path,
                        execution_environment=display_lease.environment,
                        on_progress=lambda entry: self._handle_progress(entry, job.id),
                    )
            else:
                result = await self.executor.execute(
                    job=job,
                    package_path=package_path,
                    on_progress=lambda entry: self._handle_progress(entry, job.id),
                )

            # Report completion
            await self.client.complete_run(result)

            logger.info(
                "Job completed",
                run_id=job.id,
                status=result.status,
                duration_ms=result.duration_ms,
            )

        except Exception as e:
            logger.exception("Job execution failed", run_id=job.id, error=str(e))

            # Report failure
            await self.client.complete_run(
                RunResult(
                    run_id=job.id,
                    status=RunStatus.FAILED,
                    started_at=datetime.utcnow(),
                    completed_at=datetime.utcnow(),
                    duration_ms=0,
                    steps_completed=0,
                    steps_failed=1,
                    error=str(e),
                    logs=[f"Execution error: {e}"],
                    artifacts=[],
                )
            )

        finally:
            self._active_jobs.pop(job.id, None)
            self.current_job = next(iter(self._active_jobs.values()), None)

    async def _download_package(self, job: Job) -> str:
        """Download bot package to temp file."""
        # Create temp file for package
        temp_dir = Path(tempfile.gettempdir()) / "skuldbot-packages"
        temp_dir.mkdir(exist_ok=True)

        if job.package_url:
            package_path = temp_dir / f"{job.id}.zip"
            await self.client.download_package(job.package_url, str(package_path))
            return str(package_path)

        raise RuntimeError(
            f"Run {job.id} has no package URL. Runner requires pre-built .skb package dispatch."
        )

    async def _handle_progress(self, entry: LogEntry | StepProgress, run_id: str):
        """Handle progress updates from executor - either logs or step progress."""
        try:
            if isinstance(entry, LogEntry):
                # Send log entry for real-time streaming
                await self.client.send_log(entry)
            elif isinstance(entry, StepProgress):
                # Send step progress
                await self.client.report_progress(
                    ProgressReport(
                        run_id=entry.run_id or run_id,
                        status=RunStatus.RUNNING,
                        steps=[entry],
                    )
                )
        except Exception as e:
            # Don't fail execution if progress reporting fails
            logger.debug("Failed to send progress", error=str(e))

    def _detect_graphical_capabilities(self):
        pool = self._linux_virtual_display_pool
        return build_graphical_capabilities(
            GraphicalProbeInput(
                platform_system=platform.system(),
                environment=os.environ,
                max_graphical_sessions=(
                    pool.max_sessions if pool is not None else self.config.max_graphical_sessions
                ),
                current_graphical_sessions=pool.active_count if pool is not None else 0,
            )
        )

    @staticmethod
    def _requires_linux_virtual_display(job: Job) -> bool:
        return job_requires_linux_virtual_display(job)
