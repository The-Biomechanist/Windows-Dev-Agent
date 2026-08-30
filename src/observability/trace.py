"""Minimal provenance-bound audit state for Windows Dev Agent hooks.

Raw commands, tool inputs, stdout/stderr, and arbitrary responses are never
persisted. Tool responses may be inspected in memory only to derive the small
result-status/execution-outcome fields consumed by audit summaries. Audit
retention is bounded to the current log plus one rotated predecessor.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterator, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = ROOT / ".cache"
MAX_LOG_BYTES = 2 * 1024 * 1024
AUDIT_SCHEMA_VERSION = 1
_LOCK_TIMEOUT_SECONDS = 2.0


def resolve_log_file(data_dir: Optional[str] = None) -> Path:
    configured = data_dir or os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR")
    directory = Path(configured).expanduser() if configured else DEFAULT_DATA_DIR
    return directory / "agent.log"


def history_log_files(log_file: Path) -> list[Path]:
    """Return retained audit files in chronological order."""
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


def _short_tool_name(payload: dict[str, Any]) -> str:
    return str(payload.get("tool_name", "")).rsplit("__", 1)[-1]


def _may_execute_external_process(payload: dict[str, Any], tool_input: dict[str, Any]) -> bool:
    """Return whether this call could have started an external process."""
    tool_name = _short_tool_name(payload)
    if tool_name in {"capability_run", "package_install", "sandbox_run"}:
        return tool_input.get("execute") is True
    if tool_name in {"env_inspect", "tool_discover", "package_search"}:
        return True
    if tool_name == "ecosystem_scan":
        return tool_input.get("include_host") is True
    return False


def derive_execution_outcome(payload: dict[str, Any]) -> tuple[str, Optional[str]]:
    """Derive only what the returned tool evidence establishes."""
    raw_tool_input = payload.get("tool_input") or {}
    tool_input = raw_tool_input if isinstance(raw_tool_input, dict) else {}
    has_execute = "execute" in tool_input
    requested_execute = tool_input.get("execute") is True

    hook_event = str(payload.get("hook_event_name", ""))
    result = _decode_result_payload(payload.get("tool_response"))
    status = str(result.get("status")) if isinstance(result, dict) and result.get("status") is not None else None

    if has_execute and not requested_execute:
        return "not_executed", status or "planned"

    if result is None:
        if hook_event == "PostToolUseFailure":
            return ("unknown", None) if _may_execute_external_process(payload, tool_input) else ("not_applicable", None)
        if has_execute and requested_execute:
            return "unknown", None
        if _may_execute_external_process(payload, tool_input):
            return "unknown", None
        return "not_applicable", None

    if (
        has_execute
        and requested_execute
        and result.get("execution_started") is True
        and result.get("timed_out") is True
    ):
        return "unknown", status

    if status == "completed":
        succeeded = result.get("succeeded")
        if succeeded is True:
            return "succeeded", status
        if succeeded is False:
            return "failed", status
        returncode = result.get("returncode")
        if isinstance(returncode, int) and not isinstance(returncode, bool):
            return ("succeeded" if returncode == 0 else "failed"), status
        return "unknown", status
    if status == "failed":
        return ("failed" if result.get("execution_started") is not False else "not_executed"), status
    if status == "launched":
        return "unknown", status
    if status in {
        "planned",
        "blocked",
        "unavailable",
        "invalid_input",
        "approval_required",
        "unknown_capability",
        "configuration_error",
    }:
        return "not_executed", status

    if has_execute and requested_execute:
        return "unknown", status
    return "not_applicable", status


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
        "schema_version": AUDIT_SCHEMA_VERSION,
        "ts": datetime.now(timezone.utc).isoformat(),
        "lifecycle_event": hook_event,
        "lifecycle_success": lifecycle_success,
        # Legacy mirrors remain readable while consumers migrate to explicit lifecycle fields.
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
    if not target.exists() or target.stat().st_size < MAX_LOG_BYTES:
        return
    backup = target.with_name(target.name + ".1")
    backup.unlink(missing_ok=True)
    target.replace(backup)


@contextmanager
def _interprocess_log_lock(target: Path) -> Iterator[None]:
    """Serialize rotate+append between independent Windows hook processes."""
    if os.name != "nt":
        yield
        return
    import msvcrt

    lock_path = target.with_name(target.name + ".lock")
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
        acquired = False
        while not acquired:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out acquiring audit log lock")
                time.sleep(0.01)
        try:
            yield
        finally:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass


def _versioned_event(event: dict[str, Any]) -> dict[str, Any]:
    record = dict(event)
    record.setdefault("schema_version", AUDIT_SCHEMA_VERSION)
    if "lifecycle_event" not in record and "event" in record:
        record["lifecycle_event"] = record.get("event")
    if "lifecycle_success" not in record and "success" in record:
        record["lifecycle_success"] = record.get("success")
    return record


def append_event(event: dict[str, Any], log_file: Optional[Path] = None) -> None:
    target = log_file or resolve_log_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    record = _versioned_event(event)
    with _interprocess_log_lock(target):
        _rotate_if_needed(target)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            handle.flush()


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
