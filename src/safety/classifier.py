"""Host-neutral Windows Dev Agent safety classification."""

from __future__ import annotations

import re
from typing import Any, Optional

from src.capabilities import CapabilityConfigError, load_capabilities

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
    re.compile(r"\b(Invoke-Expression|Start-Process)\b", re.I),
    re.compile(r"\b(rm|del|erase|rmdir)\b", re.I),
]
FORBIDDEN_PATTERNS = [
    re.compile(r"\b(format\s+[a-z]:|diskpart\b|bcdedit\b|cipher\s+/w:)", re.I),
    re.compile(r"\bRemove-Item\b.*(HKLM:|C:\\Windows\\System32)", re.I),
]
DYNAMIC_SHELL = re.compile(r"(;|\r|\n|&&|\|\||\||>|<|\$\(|@\(|`|(?<!&)&(?!&))")
MCP_PREFIXES = (
    "mcp__windows-dev-agent__",
    "mcp__plugin_windows-dev-agent_windows-dev-agent__",
)


def classify_shell(command: str) -> str:
    for pattern in FORBIDDEN_PATTERNS:
        if pattern.search(command):
            return "forbidden"
    for pattern in APPROVAL_PATTERNS:
        if pattern.search(command):
            return "approval-required"
    if DYNAMIC_SHELL.search(command):
        return "approval-required"
    for pattern in READ_ONLY_PATTERNS:
        if pattern.search(command):
            return "read-only"
    for pattern in REVERSIBLE_PATTERNS:
        if pattern.search(command):
            return "reversible"
    return "approval-required"


def classify_bash(command: str) -> str:
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

    if short_name in {"env_inspect", "workflow_plan", "logs_query"}:
        return "read-only"
    if short_name in {"tool_discover", "package_search"}:
        return "approval-required"
    if short_name == "ecosystem_scan":
        return "approval-required" if tool_input.get("include_host") is True else "read-only"
    if short_name == "mcp_audit":
        broader = tool_input.get("include_host") is True or bool(str(tool_input.get("config_path", "")).strip())
        return "approval-required" if broader else "read-only"

    execute = tool_input.get("execute") is True
    if short_name in {"package_install", "sandbox_run"}:
        return "approval-required" if execute else "read-only"
    if short_name == "capability_run":
        if not execute:
            return "read-only"
        return _capability_safety(str(tool_input.get("capability", "")), tool_input.get("extra_args"))
    return "approval-required"
