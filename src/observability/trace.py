"""Structured JSONL audit logger for Claude Code tool hooks."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_FILE = ROOT / "agent.log"
SENSITIVE_KEY = re.compile(r"(password|passwd|secret|token|api[_-]?key|authorization|cookie|credential)", re.I)
MAX_VALUE_CHARS = 4000


def _redact(value: Any, key: str = "") -> Any:
    if key and SENSITIVE_KEY.search(key):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value[:100]]
    if isinstance(value, str):
        return value if len(value) <= MAX_VALUE_CHARS else value[:MAX_VALUE_CHARS] + "…<truncated>"
    return value


def event_from_hook(payload: dict[str, Any]) -> dict[str, Any]:
    hook_event = str(payload.get("hook_event_name", "unknown"))
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": hook_event,
        "success": hook_event != "PostToolUseFailure",
        "session_id": payload.get("session_id"),
        "tool_name": payload.get("tool_name"),
        "tool_use_id": payload.get("tool_use_id"),
        "duration_ms": payload.get("duration_ms"),
        "tool_input": _redact(payload.get("tool_input") or {}),
        "tool_response": _redact(payload.get("tool_response") or {}),
        "error": _redact(payload.get("error"), "error") if payload.get("error") else None,
    }


def append_event(event: dict[str, Any], log_file: Path = LOG_FILE) -> None:
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be a JSON object")
        append_event(event_from_hook(payload))
    except Exception as exc:
        # Observability must never break the tool call it is observing.
        print(f"windows-dev-agent trace warning: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
