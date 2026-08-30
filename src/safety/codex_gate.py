"""Codex PreToolUse adapter for Windows Dev Agent safety classification.

Codex does not support Claude Code's ``permissionDecision: ask`` semantics in
PreToolUse. This adapter therefore denies only actions the shared classifier
marks forbidden and otherwise defers to Codex's native approval policy.
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
from src.runtime_paths import resolve_codex_data_dir
from src.safety.gate import classify_tool_call

CODEX_MCP_PREFIX = "mcp__windows_dev_agent__"
SHARED_MCP_PREFIX = "mcp__windows-dev-agent__"


def _shared_tool_name(tool_name: str) -> str:
    if tool_name.startswith(CODEX_MCP_PREFIX):
        return SHARED_MCP_PREFIX + tool_name[len(CODEX_MCP_PREFIX):]
    return tool_name


def evaluate_hook_event(event: dict[str, Any], *, log_file: Optional[Path] = None) -> Optional[dict[str, Any]]:
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    safety_class = classify_tool_call(_shared_tool_name(tool_name), tool_input)
    denied = safety_class == "forbidden"
    target_log = log_file or resolve_log_file(str(resolve_codex_data_dir()))
    try:
        append_event(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "PreToolUse",
                "success": None,
                "execution_outcome": "not_executed",
                "permission_denied": denied,
                "session_id": event.get("session_id"),
                "tool_name": tool_name,
                "tool_use_id": event.get("tool_use_id"),
                "safety_class": safety_class,
                "permission_decision": "deny" if denied else "host-default",
                "host": "codex",
            },
            target_log,
        )
    except Exception:
        pass
    if not denied:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Windows Dev Agent blocked a forbidden action. Change the task boundary explicitly before retrying.",
            "additionalContext": "windows-dev-agent host=codex safety_class=forbidden",
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
                "permissionDecisionReason": f"Windows Dev Agent Codex safety hook could not parse its input: {exc}",
            }
        }))
        return 0
    log_file = resolve_log_file(args.data_dir) if args.data_dir else None
    output = evaluate_hook_event(event, log_file=log_file)
    if output is not None:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
