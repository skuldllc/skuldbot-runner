# Copyright (c) 2026 Skuld, LLC. All rights reserved.
# Proprietary and confidential. Reverse engineering prohibited.

"""Isolated runtime worker process for one runner job."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

LOCAL_OUTPUT_PATH_KEYS = {
    "log_html",
    "output_xml",
    "report_html",
}
WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/].+")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-dir", required=True)
    parser.add_argument("--inputs-json", required=True)
    parser.add_argument("--result-json", required=True)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--bot-name", required=True)
    args = parser.parse_args()

    try:
        Executor, ExecutionMode = _resolve_runtime_executor()
        inputs = json.loads(Path(args.inputs_json).read_text(encoding="utf-8"))
        runtime = Executor(mode=ExecutionMode.PRODUCTION)
        result = runtime.run_from_package(
            args.package_dir,
            variables=inputs if isinstance(inputs, dict) else {},
            execution_id=args.execution_id,
            bot_id=args.bot_id,
            bot_name=args.bot_name,
        )
        payload = _serialize_runtime_result(result)
        Path(args.result_json).write_text(json.dumps(payload), encoding="utf-8")
        return 0
    except Exception as exc:
        Path(args.result_json).write_text(
            json.dumps(
                {
                    "success": False,
                    "output": {},
                    "logs": [],
                    "errors": [{"message": str(exc)}],
                }
            ),
            encoding="utf-8",
        )
        print(str(exc), file=sys.stderr)
        return 1


def _serialize_runtime_result(result: Any) -> dict[str, Any]:
    return {
        "success": bool(getattr(result, "success", False)),
        "output": _sanitize_runtime_output(getattr(result, "output", {}) or {}),
        "logs": [_serialize_log(entry) for entry in (getattr(result, "logs", []) or [])],
        "errors": [
            _serialize_error(error) for error in (getattr(result, "errors", []) or [])
        ],
    }


def _sanitize_runtime_output(value: Any) -> Any:
    """Remove local filesystem paths from run outputs before completion."""

    sanitized = _sanitize_output_value(value, key=None)
    return sanitized if isinstance(sanitized, dict) else {}


def _sanitize_output_value(value: Any, key: str | None) -> Any:
    if key in LOCAL_OUTPUT_PATH_KEYS:
        return None

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for child_key, child_value in value.items():
            sanitized = _sanitize_output_value(child_value, key=str(child_key))
            if sanitized is not None:
                result[str(child_key)] = sanitized
        return result

    if isinstance(value, list):
        result = []
        for child_value in value:
            sanitized = _sanitize_output_value(child_value, key=None)
            if sanitized is not None:
                result.append(sanitized)
        return result

    if isinstance(value, str) and _looks_like_local_path(value):
        return None

    return value


def _looks_like_local_path(value: str) -> bool:
    stripped = value.strip()
    return stripped.startswith("/") or bool(WINDOWS_ABSOLUTE_PATH.match(stripped))


def _serialize_log(entry: Any) -> dict[str, str]:
    return {"message": str(getattr(entry, "message", entry))}


def _serialize_error(error: Any) -> dict[str, str]:
    if isinstance(error, dict):
        return {"message": str(error.get("message", error))}
    return {"message": str(error)}


def _resolve_runtime_executor() -> tuple[Any, Any]:
    try:
        from skuldbot import ExecutionMode, Executor

        return Executor, ExecutionMode
    except ImportError:
        env_path = os.environ.get("SKULDBOT_EXECUTOR_PY_PATH")
        if env_path:
            candidate = Path(env_path).expanduser()
            if (candidate / "skuldbot").exists():
                candidate_str = str(candidate)
                if candidate_str not in sys.path:
                    sys.path.insert(0, candidate_str)
                from skuldbot import ExecutionMode, Executor

                return Executor, ExecutionMode

    raise RuntimeError(
        "Runtime package `skuldbot-executor` not found. "
        "Set SKULDBOT_EXECUTOR_PY_PATH or install the package in runner environment."
    )


if __name__ == "__main__":
    raise SystemExit(main())
