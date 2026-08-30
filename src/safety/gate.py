"""Claude Code PreToolUse safety gate.

The gate classifies the effective requested action and returns Claude Code's
structured permission decision. Only actions proven read-only are auto-allowed.
Reversible/project-code execution, approval-required actions, and uncertain
compound commands ask the human. Forbidden actions are denied.

A model-provided ``user_approved`` flag never bypasses the host permission
surface.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.capabilities import CapabilityConfigError, load_capabilities
from src.observability.trace import append_event, resolve_log_file

READ_ONLY_PATTERNS = [
    re.compile(r"^\s*(git\s+(status|log|diff|show)(\s|$)|Get-[\w-]+(\s|$)|Test-Path(\s|$)|Resolve-Path(\s|$)|where(\.exe)?\b)", re.I),
    re.compile(r"^\s*(python|py|node|npm|git|gh|dotnet|cargo|rustc|go|java|winget|choco|scoop)\s+--?version\b", re.I),
]

REVERSIBLE_PATTERNS = [
    re.compile(r"^\s*(pytest|ruff\b|pylint\b|eslint\b|dprint\s+check\b)", re.I),
    re.compile(r"^\s*(cargo|go|dotnet)\s+test\b", re.I),
    re.compile(r"^\s*dotnet\s+build\b", re.I),
]

APPROVAL_PATTERNS = [
    re.compile(r"\b(winget|choco|scoop)\s+(install|upgrade|uninstall)\b", re.I),
    re.compile(r"\b(pip|uv|npm|pnpm|yarn|cargo)\s+(install|add|remove|uninstall|update|upgrade)\b", re.I),
    re.compile(r"\bgit\s+(push|commit|merge|rebase|reset|clean)\b", re.I),
    re.compile(r"\bgh\s+(pr\s+create|pr\s+merge|release\s+create)\b", re.I),
    re.compile(r"\b(reg\s+(add|delete)|Set-Item(Property)?\b|New-Item(Property)?\b|Remove-Item\b|Enable-WindowsOptionalFeature\b|Disable-WindowsOptionalFeature\b)", re.I),
    re.compile(r"\b(rm|del|erase|rmdir)\b", re.I),
]

FORBIDDEN_PATTERNS = [
    re.compile(r"\b(format\s+[a-z]:|diskpart\b|bcdedit\b|cipher\s+/w:)", re.I),
    re.compile(r"\bRemove-Item\b.*(HKLM:|C:\\\\Windows\\\\System32)", re.I),
]

COMPOUND_COMMAND = re.compile(r"(;|\r|\n|&&|\|\||\|)")

# Claude Code scopes MCP tools from a plugin-bundled server. Keep the bare
# server prefix as well for direct/local MCP execution and tests.
MCP_PREFIXES = (
    "mcp__windows-dev-agent__",
    "mcp__plugin_windows-dev-agent_windows-dev-agent__",
)


def classify_shell(command: str) -> str:
    """Classify one Bash or PowerShell command conservatively."""
    for pattern in FORBIDDEN_PATTERNS:
        if pattern.search(command):
            return "forbidden"
    for pattern in APPROVAL_PATTERNS:
        if pattern.search(command):
            return "approval-required"
    if COMPOUND_COMMAND.search(command):
        return "approval-required"
    for pattern in READ_ONLY_PATTERNS:
        if pattern.search(command):
            return "read-only"
    for pattern in REVERSIBLE_PATTERNS:
        if pattern.search(command):
            return "reversible"
    return "approval-required"


def classify_bash(command: str) -> str:
    """Backward-compatible wrapper used by tests and callers."""
    return classify_shell(command)


def _capability_safety(capability_id: str, extra_args: Any = None) -> str:
    try:
        capability = load_capabilities().get(capability_id)
    except CapabilityConfigError:
        return "approval-required"
    if capability is None:
        return "approval-required"
    if capability.forbidden:
        return "forbidden"
    if isinstance(extra_args, list) and extra_args:
        return "approval-required"
    return capability.safety


def _mcp_short_name(tool_name: str) -> Optional[str]:
    for prefix in MCP_PREFIXES:
        if tool_name.startswith(prefix):
            return tool_name[len(prefix):]
    return None


def classify_tool_call(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name in {"Bash", "PowerShell"}:
        return classify_shell(str(tool_input.get("command", "")))

    short_name = _mcp_short_name(tool_name)
    if short_name is None:
        return "approval-required"

    if short_name in {
        "env_inspect",
        "tool_discover",
        "workflow_plan",
        "package_search",
        "logs_query",
        "mcp_audit",
        "ecosystem_scan",
    }:
        return "read-only"

    execute = bool(tool_input.get("execute", False))
    if short_name in {"package_install", "sandbox_run"}:
        return "approval-required" if execute else "read-only"

    if short_name == "capability_run":
        if not execute:
            return "read-only"
        return _capability_safety(
            str(tool_input.get("capability", "")),
            tool_input.get("extra_args"),
        )

    return "approval-required"


def _decision(safety_class: str) -> tuple[str, str]:
    if safety_class == "read-only":
        return "allow", "Windows Dev Agent proved this action read-only."
    if safety_class in {"reversible", "approval-required", "checkpoint"}:
        return "ask", f"Windows Dev Agent requires the ordinary host permission decision for this {safety_class} action."
    return "deny", "Windows Dev Agent blocked a forbidden action. Change the task boundary explicitly before retrying."


def evaluate_hook_event(
    event: dict[str, Any], *, log_file: Optional[Path] = None
) -> dict[str, Any]:
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    safety_class = classify_tool_call(tool_name, tool_input)
    decision, reason = _decision(safety_class)
    try:
        append_event(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "PreToolUse",
                "success": decision != "deny",
                "session_id": event.get("session_id"),
                "tool_name": tool_name,
                "tool_use_id": event.get("tool_use_id"),
                "safety_class": safety_class,
                "permission_decision": decision,
            },
            log_file,
        )
    except Exception:
        pass

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
    except Exception as exc:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Safety hook could not parse its input: {exc}",
            }
        }
        print(json.dumps(output))
        return 0

    print(
        json.dumps(
            evaluate_hook_event(
                event,
                log_file=resolve_log_file(args.data_dir),
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
