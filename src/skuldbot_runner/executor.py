# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Bot package executor using Robot Framework."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

import structlog

from .config import RunnerConfig
from .graphical_runtime import build_display_lease_environment
from .models import Job, LogEntry, LogLevel, RunResult, RunStatus, StepProgress

logger = structlog.get_logger()

# Type for progress callback
ProgressCallback = Callable[[StepProgress | LogEntry], Awaitable[None]]
WINDOWS_SESSION_ID_ENV = "SKULDBOT_WINDOWS_SESSION_ID"
WINDOWS_BROKER_COMMAND_ENV = "SKULDBOT_WINDOWS_SESSION_BROKER_COMMAND"
WINDOWS_ROBOT_USER_REF_ENV = "SKULDBOT_WINDOWS_ROBOT_USER_REF"
WINDOWS_CREDENTIAL_REF_KEY_ENV = "SKULDBOT_WINDOWS_SESSION_CREDENTIAL_REF_KEY"
WINDOWS_PROFILE_REF_ENV = "SKULDBOT_WINDOWS_SESSION_PROFILE_REF"
WINDOWS_TEMP_ROOT_REF_ENV = "SKULDBOT_WINDOWS_SESSION_TEMP_ROOT_REF"
WINDOWS_DOWNLOADS_ROOT_REF_ENV = "SKULDBOT_WINDOWS_SESSION_DOWNLOADS_ROOT_REF"


class BotExecutor:
    """Executes bot packages using Robot Framework."""

    def __init__(self, config: RunnerConfig):
        self.config = config
        self.work_dir = Path(config.work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)

    async def execute(
        self,
        job: Job,
        package_path: str,
        execution_environment: dict[str, str] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> RunResult:
        """Execute a bot package and return the result."""
        started_at = datetime.utcnow()
        run_dir = self.work_dir / job.id
        logs: list[str] = []
        artifacts: list[str] = []

        async def emit_log(
            message: str,
            level: LogLevel = LogLevel.INFO,
            node_id: str | None = None,
        ):
            """Emit a log entry to the progress callback."""
            log_entry = LogEntry(
                run_id=job.id,
                timestamp=datetime.utcnow(),
                level=level,
                message=message,
                node_id=node_id,
            )
            logs.append(message)
            if on_progress:
                await on_progress(log_entry)

        try:
            # 1. Extract package
            logger.info("Extracting bot package", run_id=job.id)
            await emit_log("Extracting bot package...")
            extract_dir = self._extract_package(package_path, run_dir)
            await emit_log(f"Package extracted to {extract_dir.name}")

            # 2. Install dependencies if requirements.txt exists
            requirements_file = extract_dir / "requirements.txt"
            if requirements_file.exists():
                logger.info("Installing dependencies", run_id=job.id)
                await emit_log("Installing dependencies from requirements.txt...")
                await self._install_dependencies(requirements_file)
                await emit_log("Dependencies installed successfully")

            # 3. Execute through shared runtime package (skuldbot-executor)
            await emit_log("Starting runtime execution...")
            runtime_result = await self._run_runtime_worker(
                job=job,
                extract_dir=extract_dir,
                execution_environment=execution_environment,
            )

            # 6. Parse results
            completed_at = datetime.utcnow()
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            # Collect artifacts
            output_dir = extract_dir / "output"
            for artifact_file in output_dir.glob("*"):
                artifacts.append(str(artifact_file))

            runtime_logs = getattr(runtime_result, "logs", []) or []
            for log_entry in runtime_logs:
                message = (
                    log_entry.get("message", str(log_entry))
                    if isinstance(log_entry, dict)
                    else getattr(log_entry, "message", str(log_entry))
                )
                if message:
                    logs.append(message)

            runtime_errors = getattr(runtime_result, "errors", []) or []
            runtime_error_message = None
            if runtime_errors:
                runtime_error_message = "; ".join(
                    str(e.get("message", e)) if isinstance(e, dict) else str(e)
                    for e in runtime_errors
                )

            runtime_success = bool(getattr(runtime_result, "success", False))

            # Emit completion log
            if runtime_success:
                await emit_log(
                    "Execution completed successfully."
                )
            else:
                await emit_log(
                    f"Execution failed. Error: {runtime_error_message or 'Unknown error'}",
                    LogLevel.ERROR
                )

            return RunResult(
                run_id=job.id,
                status=RunStatus.SUCCESS if runtime_success else RunStatus.FAILED,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                steps_completed=0,
                steps_failed=0 if runtime_success else 1,
                output=getattr(runtime_result, "output", {}) or {},
                error=runtime_error_message,
                logs=logs,
                artifacts=artifacts,
            )

        except Exception as e:
            logger.exception("Bot execution failed", run_id=job.id, error=str(e))
            completed_at = datetime.utcnow()
            duration_ms = int((completed_at - started_at).total_seconds() * 1000)

            await emit_log(f"Fatal error: {str(e)}", LogLevel.ERROR)

            return RunResult(
                run_id=job.id,
                status=RunStatus.FAILED,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                steps_completed=0,
                steps_failed=1,
                output={},
                error=str(e),
                logs=logs,
                artifacts=artifacts,
            )

        finally:
            # Cleanup (optional - keep for debugging)
            if os.environ.get("SKULDBOT_CLEANUP_RUNS", "true").lower() == "true":
                self._cleanup(run_dir)

    def _extract_package(self, package_path: str, run_dir: Path) -> Path:
        """Extract bot package zip to run directory."""
        run_dir.mkdir(parents=True, exist_ok=True)
        extract_dir = run_dir / "bot"

        with zipfile.ZipFile(package_path, "r") as zf:
            zf.extractall(extract_dir)

        return extract_dir

    async def _install_dependencies(self, requirements_file: Path) -> None:
        """Install Python dependencies from requirements.txt."""
        process = subprocess.run(
            ["pip", "install", "-r", str(requirements_file), "--quiet"],
            capture_output=True,
            text=True,
        )
        if process.returncode != 0:
            logger.warning(
                "Failed to install some dependencies",
                stderr=process.stderr,
            )

    def _prepare_variables(self, inputs: dict[str, Any]) -> list[str]:
        """Convert inputs to Robot Framework variable arguments."""
        variables = []
        for key, value in inputs.items():
            # Robot Framework uses -v NAME:value format
            if isinstance(value, (dict, list)):
                import json
                value = json.dumps(value)
            variables.extend(["-v", f"{key}:{value}"])
        return variables

    async def _run_robot(
        self,
        job_id: str,
        robot_file: Path,
        output_dir: Path,
        variables: list[str],
        cwd: Path,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Execute Robot Framework and return results with real-time log streaming."""
        cmd = [
            "robot",
            "--outputdir", str(output_dir),
            "--output", "output.xml",
            "--log", "log.html",
            "--report", "report.html",
            "--console", "verbose",  # More detailed console output
            *variables,
            str(robot_file),
        ]

        if robot_file.suffix and robot_file.suffix != ".robot":
            cmd[1:1] = ["--extension", robot_file.suffix.lstrip(".")]

        logger.debug("Running robot command", cmd=" ".join(cmd))

        # Use asyncio subprocess for real-time streaming
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        collected_logs: list[str] = []

        async def stream_output(stream: asyncio.StreamReader, is_stderr: bool = False):
            """Stream output line by line and emit to callback."""
            while True:
                line = await stream.readline()
                if not line:
                    break

                text = line.decode("utf-8", errors="replace").rstrip()
                if not text:
                    continue

                collected_logs.append(text)

                # Emit to progress callback
                if on_progress:
                    # Determine log level from content
                    level = LogLevel.INFO
                    if is_stderr or "FAIL" in text or "ERROR" in text:
                        level = LogLevel.ERROR
                    elif "WARN" in text:
                        level = LogLevel.WARN
                    elif "DEBUG" in text:
                        level = LogLevel.DEBUG

                    log_entry = LogEntry(
                        run_id=job_id,
                        timestamp=datetime.utcnow(),
                        level=level,
                        message=text,
                    )
                    await on_progress(log_entry)

        # Stream stdout and stderr concurrently
        await asyncio.gather(
            stream_output(process.stdout, is_stderr=False),
            stream_output(process.stderr, is_stderr=True),
        )

        # Wait for process to complete
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=self.config.job_timeout_seconds
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            raise TimeoutError(
                f"Robot execution timed out after {self.config.job_timeout_seconds}s"
            )

        # Parse output.xml for detailed results
        output_xml = output_dir / "output.xml"
        result = self._parse_robot_output(output_xml)
        result["logs"] = collected_logs

        return result

    def _parse_robot_output(self, output_xml: Path) -> dict[str, Any]:
        """Parse Robot Framework output.xml for results."""
        if not output_xml.exists():
            return {
                "success": False,
                "error": "No output.xml generated",
                "passed": 0,
                "failed": 1,
            }

        try:
            # Simple XML parsing - in production use robot.api.ExecutionResult
            import xml.etree.ElementTree as ET

            tree = ET.parse(output_xml)
            root = tree.getroot()

            # Find statistics
            stats = root.find(".//statistics/total/stat")
            if stats is not None:
                passed = int(stats.get("pass", 0))
                failed = int(stats.get("fail", 0))
            else:
                passed = 0
                failed = 0

            # Check for errors
            errors = root.findall(".//msg[@level='FAIL']")
            error_msgs = [e.text for e in errors if e.text]

            return {
                "success": failed == 0,
                "passed": passed,
                "failed": failed,
                "error": "; ".join(error_msgs[:5]) if error_msgs else None,
                "output": {},
            }

        except Exception as e:
            logger.warning("Failed to parse output.xml", error=str(e))
            return {
                "success": False,
                "error": f"Failed to parse results: {e}",
                "passed": 0,
                "failed": 1,
            }

    def _cleanup(self, run_dir: Path) -> None:
        """Clean up run directory."""
        try:
            if run_dir.exists():
                shutil.rmtree(run_dir)
        except Exception as e:
            logger.warning("Failed to cleanup run directory", error=str(e))

    def _find_entry_file(self, extract_dir: Path) -> Path | None:
        """Find the main executable file from an extracted package."""
        direct_candidates = [extract_dir / "main.skb", extract_dir / "main.robot"]
        for candidate in direct_candidates:
            if candidate.exists():
                return candidate

        for name in ("main.skb", "main.robot"):
            recursive = next(extract_dir.rglob(name), None)
            if recursive:
                return recursive

        return None

    async def _run_runtime_worker(
        self,
        job: Job,
        extract_dir: Path,
        execution_environment: dict[str, str] | None,
    ) -> Any:
        """Run the execution runtime in an isolated subprocess."""

        result_path = extract_dir / "runtime-result.json"
        inputs_path = extract_dir / "runtime-inputs.json"
        inputs_path.write_text(json.dumps(job.inputs), encoding="utf-8")

        env = self._build_runtime_worker_environment(job, execution_environment)

        command = self._build_runtime_worker_command(
            extract_dir=extract_dir,
            inputs_path=inputs_path,
            result_path=result_path,
            job=job,
        )
        command = self._wrap_windows_session_broker_command(command, env)

        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.config.job_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise TimeoutError(
                f"Runtime worker timed out after {self.config.job_timeout_seconds}s"
            ) from exc
        if process.returncode != 0:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            stdout_text = stdout.decode("utf-8", errors="replace").strip()
            raise RuntimeError(
                "Runtime worker failed: "
                f"{stderr_text or stdout_text or f'exit {process.returncode}'}"
            )

        if not result_path.exists():
            raise RuntimeError("Runtime worker did not write a result file.")

        payload = json.loads(result_path.read_text(encoding="utf-8"))
        return _RuntimeResult(payload)

    def _build_runtime_worker_command(
        self,
        *,
        extract_dir: Path,
        inputs_path: Path,
        result_path: Path,
        job: Job,
    ) -> list[str]:
        """Build the local runtime worker command."""

        return [
            sys.executable,
            "-m",
            "skuldbot_runner.runtime_worker",
            "--package-dir",
            str(extract_dir),
            "--inputs-json",
            str(inputs_path),
            "--result-json",
            str(result_path),
            "--execution-id",
            job.id,
            "--bot-id",
            job.bot_id or job.id,
            "--bot-name",
            job.bot_name,
        ]

    def _wrap_windows_session_broker_command(
        self,
        command: list[str],
        env: dict[str, str],
    ) -> list[str]:
        """Route Windows session execution through the configured host broker."""

        session_id = env.get(WINDOWS_SESSION_ID_ENV, "").strip()
        if not session_id:
            return command

        broker_command = env.get(WINDOWS_BROKER_COMMAND_ENV, "").strip()
        robot_user_ref = env.get(WINDOWS_ROBOT_USER_REF_ENV, "").strip()
        credential_ref_key = env.get(WINDOWS_CREDENTIAL_REF_KEY_ENV, "").strip()
        if not broker_command:
            raise RuntimeError(
                "Windows interactive session execution requires a broker command."
            )
        if not robot_user_ref or not credential_ref_key:
            raise RuntimeError(
                "Windows interactive session execution requires user and credential refs."
            )

        broker_args = [
            broker_command,
            "--session-id",
            session_id,
            "--robot-user-ref",
            robot_user_ref,
            "--credential-ref-key",
            credential_ref_key,
        ]
        broker_args.extend(
            self._optional_windows_ref_arg("--profile-ref", env.get(WINDOWS_PROFILE_REF_ENV))
        )
        broker_args.extend(
            self._optional_windows_ref_arg("--temp-root-ref", env.get(WINDOWS_TEMP_ROOT_REF_ENV))
        )
        broker_args.extend(
            self._optional_windows_ref_arg(
                "--downloads-root-ref", env.get(WINDOWS_DOWNLOADS_ROOT_REF_ENV)
            )
        )
        return [*broker_args, "--", *command]

    @staticmethod
    def _optional_windows_ref_arg(name: str, value: str | None) -> list[str]:
        cleaned = value.strip() if isinstance(value, str) else ""
        return [name, cleaned] if cleaned else []

    def _build_runtime_worker_environment(
        self,
        job: Job,
        execution_environment: dict[str, str] | None,
    ) -> dict[str, str]:
        """Build an isolated subprocess environment for one run."""

        env = dict(os.environ)
        if execution_environment:
            env.update(execution_environment)
        if job.display_lease is not None:
            env.update(build_display_lease_environment(job.display_lease))
        return env


class _RuntimeResult:
    """Attribute wrapper for the JSON payload produced by the runtime worker."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.success = bool(payload.get("success", False))
        self.output = payload.get("output", {}) or {}
        self.logs = payload.get("logs", []) or []
        self.errors = payload.get("errors", []) or []
