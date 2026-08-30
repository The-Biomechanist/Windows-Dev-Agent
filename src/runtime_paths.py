"""Host-neutral runtime path resolution for Windows Dev Agent.

Claude injects explicit plugin data/project paths. Codex legacy plugin hooks and
MCP normalization do not expose the same data variable to both processes, so
the Codex adapter deliberately converges on one deterministic persistent root
under CODEX_HOME instead of assuming host variables are shared.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


def runtime_host(explicit: Optional[str] = None) -> str:
    value = explicit or os.environ.get("WINDOWS_DEV_AGENT_HOST", "claude")
    return value.strip().lower() or "claude"


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def resolve_codex_data_dir() -> Path:
    configured = os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return codex_home() / "plugins" / "data" / "windows-dev-agent"


def resolve_data_dir(*, host: Optional[str] = None) -> Path:
    configured = os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    if runtime_host(host) == "codex":
        return resolve_codex_data_dir()
    return ROOT / ".cache"


def resolve_project_dir(value: Optional[str] = None) -> Path:
    raw = value or os.environ.get("WINDOWS_DEV_AGENT_PROJECT_DIR")
    return Path(raw).expanduser().resolve() if raw else Path.cwd().resolve()
