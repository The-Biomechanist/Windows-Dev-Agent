"""Codex persistent path resolution for Windows Dev Agent."""

from __future__ import annotations

import os
from pathlib import Path


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def resolve_codex_data_dir() -> Path:
    configured = os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return codex_home() / "plugins" / "data" / "windows-dev-agent"
