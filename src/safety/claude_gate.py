"""Claude Code PreToolUse adapter for Windows Dev Agent safety classification.

This hook only tightens Claude Code's native permission system: forbidden
effective actions are denied, known approval-required actions ask, and ordinary
read/reversible actions defer to the host. It never returns ``allow``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.observability.trace import append_event, resolve_log_file
from src.safety.classifier import classify_tool_call


def _permission_decision(safety_class: str) -> tuple[Optional[str], str]:
    if safety_class == "forbidden":
        return "deny", "Windows Dev Agent blocked a forbidden action. Change the task boundary explicitly before retrying."
    if safety_class in {"approval-required", "checkpoint"}:
        return "ask", f"Windows Dev Agent requires explicit host confirmation for this {safety_class} action."
    return None, f"Windows Dev Agent classified this action as {safety_class} and deferred to Claude Code's normal permission flow."


def evaluate_hook_event(event: dict[str, Any], *, log_file: Optional[Path] = None) -> Optional[dict[str, Any]]:
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    safety_class = classify_tool_call(tool_name, tool_input)
    decision, reason = _permission_decision(safety_class)
    try:
        append_event(
            {
                "schema_version": 1,
                "ts": datetime.now(timezone.utc).isoformat(),
                "lifecycle_event": "PreToolUse",
                "lifecycle_success": None,
                "event": "PreToolUse",
                "success": None,
                "execution_outcome": "not_applicable",
                "permission_denied": decision == "deny",
                "session_id": event.get("session_id"),
                "tool_name": tool_name,
                "tool_use_id": event.get("tool_use_id"),
                "safety_class": safety_class,
                "permission_decision": decision or "host-default",
            },
            log_file,
        )
    except Exception:
        pass
    if decision is None:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
            "additionalContext": f"windows-dev-agent safety_class={safety_class}",
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("hook event must be a JSON object")
    except Exception as exc:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Safety hook could not parse its input: {exc}",
            }
        }))
        return 0
    output = evaluate_hook_event(event, log_file=resolve_log_file(args.data_dir))
    if output is not None:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
