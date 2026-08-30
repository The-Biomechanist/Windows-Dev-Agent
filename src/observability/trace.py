"""Minimal provenance-bound audit state for Windows Dev Agent hooks.

Raw commands, tool inputs, stdout/stderr, and arbitrary responses are never
persisted. Tool responses may be inspected in memory only to derive the small
result-status/execution-outcome fields consumed by audit summaries. Audit
retention is bounded to the current log plus one rotated predecessor.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = ROOT / ".cache"
MAX_LOG_BYTES = 2 * 1024 * 1024


def resolve_log_file(data_dir: Optional[str] = None) -> Path:
    configured = data_dir or os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR")
    directory = Path(configured).expanduser() if configured else DEFAULT_DATA_DIR
    return directory / "agent.log"


def history_log_files(log_file: Path) -> list[Path]:
    """Return retained audit files in chronological order.

    Rotation retains exactly one predecessor. Consumers must read that file
    before the current log or a mid-session rotation would make earlier retained
    events disappear from session/history views.
    """
    backup = log_file.with_name(log_file.name + ".1")
    return [path for path in (backup, log_file) if path.exists()]


def _decode_result_payload(response: Any) -> Optional[dict[str, Any]]:
    if isinstance(response, dict):
        if isinstance(response.get("status"), str):
            return response
        if isinstance(response.get("result"), dict):
            nested = _decode_result_payload(response["result"])
            if nested is not None:
                return nested
        content = response.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    try:
                        value = json.loads(item["text"])
                    except json.JSONDecodeError:
                        continue
                    if isinstance(value, dict):
                        return value
    if isinstance(response, str):
        try:
            value = json.loads(response)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None
    return None


def derive_execution_outcome(payload: dict[str, Any]) -> tuple[str, Optional[str]]:
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict) or "execute" not in tool_input:
        return "not_applicable", None
    if not bool(tool_input.get("execute", False)):
        return "not_executed", "planned"

    hook_event = str(payload.get("hook_event_name", ""))
    result = _decode_result_payload(payload.get("tool_response"))
    status = str(result.get("status")) if isinstance(result, dict) and result.get("status") is not None else None
    if hook_event == "PostToolUseFailure":
        return "failed", status
    if result is None:
        return "unknown", None

    if status == "completed":
        succeeded = result.get("succeeded")
        if succeeded is True:
            return "succeeded", status
        if succeeded is False:
            return "failed", status
        returncode = result.get("returncode")
        if isinstance(returncode, int):
            return ("succeeded" if returncode == 0 else "failed"), status
        return "unknown", status
    if status == "failed":
        return ("failed" if result.get("execution_started") is not False else "not_executed"), status
    if status == "launched":
        return "unknown", status
    if status in {
        "planned",
        "stale_plan",
        "blocked",
        "unavailable",
        "invalid_input",
        "approval_required",
        "unknown_capability",
        "configuration_error",
    }:
        return "not_executed", status
    return "unknown", status


def event_from_hook(payload: dict[str, Any]) -> dict[str, Any]:
    hook_event = str(payload.get("hook_event_name", "unknown"))
    lifecycle_success: Optional[bool]
    if hook_event == "PostToolUse":
        lifecycle_success = True
    elif hook_event == "PostToolUseFailure":
        lifecycle_success = False
    else:
        lifecycle_success = None
    execution_outcome, result_status = derive_execution_outcome(payload)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": hook_event,
        "success": lifecycle_success,
        "execution_outcome": execution_outcome,
        "result_status": result_status,
        "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id"),
        "agent_type": payload.get("agent_type"),
        "tool_name": payload.get("tool_name"),
        "tool_use_id": payload.get("tool_use_id"),
        "duration_ms": payload.get("duration_ms"),
        "error_present": bool(payload.get("error")),
    }


def _rotate_if_needed(target: Path) -> None:
    try:
        if not target.exists() or target.stat().st_size < MAX_LOG_BYTES:
            return
        backup = target.with_name(target.name + ".1")
        backup.unlink(missing_ok=True)
        target.replace(backup)
    except OSError:
        # Logging must never become an execution blocker.
        return


def append_event(event: dict[str, Any], log_file: Optional[Path] = None) -> None:
    target = log_file or resolve_log_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    _rotate_if_needed(target)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be a JSON object")
        append_event(event_from_hook(payload), resolve_log_file(args.data_dir))
    except Exception as exc:
        print(f"windows-dev-agent trace warning: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
