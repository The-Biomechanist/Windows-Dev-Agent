"""Codex PermissionRequest adapter for bounded Windows Dev Agent calls.

Codex owns prompting. This hook may auto-allow only requests whose concrete
arguments remain plan-only or project-scoped read-only; executing mutations,
host-wide inventory, and arbitrary extra config reads receive no plugin
decision and continue to Codex's normal approval UI.
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
from src.safety.codex_gate import _shared_tool_name
from src.safety.gate import classify_tool_call

PREFIX = "mcp__windows_dev_agent__"
PLAN_FIRST_TOOLS = {
    PREFIX + "capability_run",
    PREFIX + "package_install",
    PREFIX + "sandbox_run",
}


def _bounded_read(tool_name: str, tool_input: dict[str, Any]) -> bool:
    if tool_name == PREFIX + "ecosystem_scan":
        return not bool(tool_input.get("include_host", False))
    if tool_name == PREFIX + "mcp_audit":
        return not bool(tool_input.get("include_host", False)) and not bool(str(tool_input.get("config_path", "")).strip())
    return False


def evaluate_permission_request(event: dict[str, Any], *, log_file: Optional[Path] = None) -> Optional[dict[str, Any]]:
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
    elif _bounded_read(tool_name, tool_input):
        behavior = "allow"

    target_log = log_file or resolve_log_file(str(resolve_codex_data_dir()))
    try:
        append_event(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "PermissionRequest",
                "success": None,
                "execution_outcome": "not_executed",
                "permission_denied": behavior == "deny",
                "session_id": event.get("session_id"),
                "tool_name": tool_name,
                "safety_class": safety_class,
                "permission_decision": behavior or "host-default",
                "host": "codex",
            },
            target_log,
        )
    except Exception:
        pass
    if behavior is None:
        return None
    decision: dict[str, Any] = {"behavior": behavior}
    if message:
        decision["message"] = message
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}


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
    log_file = resolve_log_file(args.data_dir) if args.data_dir else None
    output = evaluate_permission_request(event, log_file=log_file)
    if output is not None:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
