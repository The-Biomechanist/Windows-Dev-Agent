"""Codex PreToolUse adapter for Windows Dev Agent safety classification.

Windows Dev Agent keeps native MCP approval policy as the prompt authority. This
adapter therefore denies only actions the shared classifier marks forbidden and
otherwise defers to Codex. PermissionRequest handles the small set of trusted
plan-only shortcuts separately.
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
from src.safety.classifier import classify_tool_call

CODEX_MCP_PREFIX = "mcp__windows_dev_agent__"
SHARED_MCP_PREFIX = "mcp__windows-dev-agent__"
CODEX_PROJECT_ARG_BY_TOOL = {
    CODEX_MCP_PREFIX + "capability_run": "cwd",
    CODEX_MCP_PREFIX + "workflow_plan": "cwd",
    CODEX_MCP_PREFIX + "sandbox_run": "workspace_folder",
    CODEX_MCP_PREFIX + "ecosystem_scan": "cwd",
    CODEX_MCP_PREFIX + "mcp_audit": "cwd",
}


def _shared_tool_name(tool_name: str) -> str:
    if tool_name.startswith(CODEX_MCP_PREFIX):
        return SHARED_MCP_PREFIX + tool_name[len(CODEX_MCP_PREFIX):]
    return tool_name


def _project_scope_error(
    event: dict[str, Any],
    tool_name: str,
    tool_input: dict[str, Any],
) -> Optional[str]:
    """Return an error unless a project-scoped call stays under Codex's host cwd."""
    argument = CODEX_PROJECT_ARG_BY_TOOL.get(tool_name)
    if argument is None:
        return None
    session_cwd = event.get("cwd")
    requested = tool_input.get(argument)
    if not isinstance(session_cwd, str) or not session_cwd.strip():
        return "Codex host project cwd was not established for this project-scoped call"
    if not isinstance(requested, str) or not requested.strip():
        return f"{argument} must be an absolute path inside the active Codex project"

    root_input = Path(session_cwd.strip()).expanduser()
    requested_input = Path(requested.strip()).expanduser()
    if not root_input.is_absolute():
        return "Codex host project cwd was not an absolute path"
    if not requested_input.is_absolute():
        return f"{argument} must be an absolute path inside the active Codex project"
    try:
        root = root_input.resolve()
        candidate = requested_input.resolve()
        if not root.is_dir():
            return "Codex host project cwd is not a directory"
        if not candidate.is_dir():
            return f"{argument} is not a directory: {candidate}"
        candidate.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return f"{argument} must stay inside the active Codex project: {root_input}"
    return None


def evaluate_hook_event(event: dict[str, Any], *, log_file: Optional[Path] = None) -> Optional[dict[str, Any]]:
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    safety_class = classify_tool_call(_shared_tool_name(tool_name), tool_input)
    scope_error = _project_scope_error(event, tool_name, tool_input)
    denied = safety_class == "forbidden" or scope_error is not None
    target_log = log_file or resolve_log_file(str(resolve_codex_data_dir()))
    try:
        append_event(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "PreToolUse",
                "success": None,
                "execution_outcome": "not_applicable",
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
    reason = (
        "Windows Dev Agent blocked a forbidden action. Change the task boundary explicitly before retrying."
        if safety_class == "forbidden"
        else f"Windows Dev Agent blocked a project-scope escape: {scope_error}"
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
            "additionalContext": (
                "windows-dev-agent host=codex safety_class=forbidden"
                if safety_class == "forbidden"
                else "windows-dev-agent host=codex project_scope=denied"
            ),
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
