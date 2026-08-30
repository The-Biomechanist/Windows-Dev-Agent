"""Codex PermissionRequest adapter for narrowly safe Windows Dev Agent plans.

Codex owns prompting. This hook may auto-allow only requests whose safety can be
established from the PermissionRequest event itself. The event supplies the
active session ``cwd``; project-scoped plan calls are auto-allowed only when the
caller-supplied absolute project path resolves inside that boundary. Plans that
enumerate project files (notably ``sandbox_run``), executing mutations, broader
reads, relative project identities, and path mismatches continue to Codex's
normal approval UI.
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
NONPROJECT_PLAN_TOOLS = {PREFIX + "package_install"}
PROJECT_PLAN_ARGS = {
    PREFIX + "capability_run": "cwd",
    PREFIX + "workflow_plan": "cwd",
}


def _inside_session_cwd(event: dict[str, Any], tool_input: dict[str, Any], argument: str) -> bool:
    """Prove an absolute caller path resolves no broader than Codex's active cwd."""
    session_cwd = str(event.get("cwd", "")).strip()
    requested = str(tool_input.get(argument, "")).strip()
    if not session_cwd or not requested:
        return False
    try:
        root = Path(session_cwd).expanduser().resolve()
        requested_path = Path(requested).expanduser()
        if not requested_path.is_absolute():
            return False
        candidate = requested_path.resolve()
        candidate.relative_to(root)
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _safe_plan_request(event: dict[str, Any], tool_name: str, tool_input: dict[str, Any]) -> bool:
    if tool_name in NONPROJECT_PLAN_TOOLS:
        return not bool(tool_input.get("execute", False))

    project_arg = PROJECT_PLAN_ARGS.get(tool_name)
    if project_arg is None:
        return False
    if tool_name.endswith("capability_run") and bool(tool_input.get("execute", False)):
        return False
    return _inside_session_cwd(event, tool_input, project_arg)


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
    elif _safe_plan_request(event, tool_name, tool_input):
        behavior = "allow"

    target_log = log_file or resolve_log_file(str(resolve_codex_data_dir()))
    try:
        append_event(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "PermissionRequest",
                "success": None,
                "execution_outcome": "not_applicable",
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
