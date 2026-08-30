"""Codex PreToolUse adapter for Windows Dev Agent safety classification.

Codex does not support Claude Code's ``permissionDecision: ask`` semantics in
PreToolUse. This adapter therefore performs only the part that can be made
host-correct: deny actions the shared classifier marks forbidden, otherwise
emit no decision and defer to Codex's native approval policy.
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
from src.safety.gate import classify_tool_call


def evaluate_hook_event(
    event: dict[str, Any], *, log_file: Optional[Path] = None
) -> Optional[dict[str, Any]]:
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    safety_class = classify_tool_call(tool_name, tool_input)
    denied = safety_class == "forbidden"
    try:
        append_event(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "PreToolUse",
                "success": None,
                "permission_denied": denied,
                "session_id": event.get("session_id"),
                "tool_name": tool_name,
                "tool_use_id": event.get("tool_use_id"),
                "safety_class": safety_class,
                "permission_decision": "deny" if denied else "host-default",
                "host": "codex",
            },
            log_file,
        )
    except Exception:
        pass

    if not denied:
        return None

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Windows Dev Agent blocked a forbidden action. Change the task "
                "boundary explicitly before retrying."
            ),
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
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Windows Dev Agent Codex safety hook could not parse its input: {exc}"
                        ),
                    }
                }
            )
        )
        return 0

    output = evaluate_hook_event(
        event,
        log_file=resolve_log_file(args.data_dir),
    )
    if output is not None:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
