"""Codex PermissionRequest adapter for plan-first Windows Dev Agent tools.

Codex MCP approval policy is tool-scoped. The three mutation-capable tools are
therefore configured to prompt by default. When Codex is about to prompt for a
plan-only call (``execute=false``), this hook can safely allow that request.
Executing calls make no hook decision and continue to the normal human prompt.
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
from src.safety.codex_gate import _shared_tool_name
from src.safety.gate import classify_tool_call

PLAN_FIRST_TOOLS = {
    "mcp__windows_dev_agent__capability_run",
    "mcp__windows_dev_agent__package_install",
    "mcp__windows_dev_agent__sandbox_run",
}


def evaluate_permission_request(
    event: dict[str, Any], *, log_file: Optional[Path] = None
) -> Optional[dict[str, Any]]:
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    safety_class = classify_tool_call(_shared_tool_name(tool_name), tool_input)
    behavior: Optional[str] = None
    message: Optional[str] = None

    if safety_class == "forbidden":
        behavior = "deny"
        message = "Windows Dev Agent blocked a forbidden action."
    elif tool_name in PLAN_FIRST_TOOLS and not bool(tool_input.get("execute", False)):
        behavior = "allow"

    try:
        append_event(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "PermissionRequest",
                "success": None,
                "permission_denied": behavior == "deny",
                "session_id": event.get("session_id"),
                "tool_name": tool_name,
                "safety_class": safety_class,
                "permission_decision": behavior or "host-default",
                "host": "codex",
            },
            log_file,
        )
    except Exception:
        pass

    if behavior is None:
        return None

    decision: dict[str, Any] = {"behavior": behavior}
    if message:
        decision["message"] = message
    return {
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": decision,
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
    except Exception:
        return 0

    output = evaluate_permission_request(
        event,
        log_file=resolve_log_file(args.data_dir),
    )
    if output is not None:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
