"""Claude Code PreToolUse safety gate.

Command hooks receive the complete hook event as JSON on stdin.  This module
classifies the requested tool call and returns Claude Code's structured
``permissionDecision`` output:

- read-only / reversible -> allow
- approval-required / checkpoint -> ask the human
- forbidden -> deny

This is deliberately host-enforced.  A model-provided ``user_approved`` flag is
not sufficient to skip the permission dialog.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any

from src.capabilities import CapabilityConfigError, load_capabilities

ROOT = Path(__file__).resolve().parent.parent.parent

READ_ONLY_PATTERNS = [
    re.compile(r"^\s*(git\s+(status|log|diff|show)|Get-[\w-]+|Test-Path|Resolve-Path|where(\.exe)?\b)", re.I),
    re.compile(r"^\s*(python|py|node|npm|git|gh|dotnet|cargo|rustc|go|java)\s+--?version\b", re.I),
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
    re.compile(r"\b(reg\s+(add|delete)|Set-ItemProperty\b|Remove-Item\b|Enable-WindowsOptionalFeature\b|Disable-WindowsOptionalFeature\b)", re.I),
    re.compile(r"\b(rm|del|erase|rmdir)\b", re.I),
]

FORBIDDEN_PATTERNS = [
    re.compile(r"\b(format\s+[a-z]:|diskpart\b|bcdedit\b|cipher\s+/w:)\b", re.I),
    re.compile(r"\bRemove-Item\b.*\b(HKLM:|C:\\\\Windows\\\\System32)\b", re.I),
]


def classify_bash(command: str) -> str:
    """Classify a shell command conservatively.

    Unknown commands ask rather than silently running.  This sacrifices a small
    amount of autonomy to prevent a missed regex from becoming an implicit
    mutation permission.
    """
    for pattern in FORBIDDEN_PATTERNS:
        if pattern.search(command):
            return "forbidden"
    for pattern in APPROVAL_PATTERNS:
        if pattern.search(command):
            return "approval-required"
    for pattern in READ_ONLY_PATTERNS:
        if pattern.search(command):
            return "read-only"
    for pattern in REVERSIBLE_PATTERNS:
        if pattern.search(command):
            return "reversible"
    return "approval-required"


def _capability_safety(capability_id: str) -> str:
    try:
        capability = load_capabilities().get(capability_id)
    except CapabilityConfigError:
        return "approval-required"
    return capability.safety if capability else "approval-required"


def classify_tool_call(tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name == "Bash":
        return classify_bash(str(tool_input.get("command", "")))

    prefix = "mcp__windows-dev-agent__"
    if not tool_name.startswith(prefix):
        return "approval-required"

    short_name = tool_name[len(prefix):]
    if short_name in {"env_inspect", "tool_discover", "workflow_plan", "logs_query", "mcp_audit", "ecosystem_scan"}:
        return "read-only"

    execute = bool(tool_input.get("execute", False))
    if short_name in {"package_install", "sandbox_run"}:
        return "approval-required" if execute else "read-only"

    if short_name == "capability_run":
        if not execute:
            return "read-only"
        return _capability_safety(str(tool_input.get("capability", "")))

    return "approval-required"


def _decision(safety_class: str) -> tuple[str, str]:
    if safety_class in {"read-only", "reversible"}:
        return "allow", f"Windows Dev Agent classified this action as {safety_class}."
    if safety_class in {"approval-required", "checkpoint"}:
        return "ask", f"Windows Dev Agent requires your confirmation for this {safety_class} action."
    return "deny", "Windows Dev Agent blocked a forbidden action. Change the task boundary explicitly before retrying."


def evaluate_hook_event(event: dict[str, Any]) -> dict[str, Any]:
    tool_name = str(event.get("tool_name", ""))
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}

    safety_class = classify_tool_call(tool_name, tool_input)
    decision, reason = _decision(safety_class)
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
            "additionalContext": f"windows-dev-agent safety_class={safety_class}",
        }
    }


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except Exception as exc:
        # A broken safety hook must fail closed, not silently allow execution.
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Safety hook could not parse its input: {exc}",
            }
        }
        print(json.dumps(output))
        return 0

    print(json.dumps(evaluate_hook_event(event)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
