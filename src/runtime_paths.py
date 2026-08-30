"""Host-neutral runtime path resolution for Windows Dev Agent.

Claude injects explicit plugin data/project paths into the MCP server. Codex
uses its plugin data directory when the host exposes it and otherwise falls
back to a stable user-writable location. Project identity is never inferred
from an installed-plugin cache path when an explicit project path is available.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


def runtime_host(explicit: Optional[str] = None) -> str:
    value = explicit or os.environ.get("WINDOWS_DEV_AGENT_HOST", "claude")
    return value.strip().lower() or "claude"


def resolve_data_dir(*, host: Optional[str] = None) -> Path:
    configured = os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR")
    if configured:
        return Path(configured).expanduser()

    if runtime_host(host) == "codex":
        plugin_data = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
        if plugin_data:
            return Path(plugin_data).expanduser()

        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "WindowsDevAgent" / "Codex"
        if os.name == "nt":
            return Path.home() / "AppData" / "Local" / "WindowsDevAgent" / "Codex"
        xdg_data = os.environ.get("XDG_DATA_HOME")
        if xdg_data:
            return Path(xdg_data).expanduser() / "windows-dev-agent" / "codex"
        return Path.home() / ".local" / "share" / "windows-dev-agent" / "codex"

    return ROOT / ".cache"


def resolve_project_dir(value: Optional[str] = None) -> Path:
    raw = value or os.environ.get("WINDOWS_DEV_AGENT_PROJECT_DIR")
    return Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()
