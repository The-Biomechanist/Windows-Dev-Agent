"""Minimal Codex PostToolUse audit adapter.

Codex PostToolUse fires after tool completion but does not provide Claude Code's
success/failure event split. Keep execution success unknown and persist no raw
tool payloads.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import sys
from typing import Any

try:
    from .trace import append_event, resolve_log_file
    from ..runtime_paths import resolve_codex_data_dir
except ImportError:
    from trace import append_event, resolve_log_file
    from src.runtime_paths import resolve_codex_data_dir


def event_from_hook(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "PostToolUse",
        "success": None,
        "session_id": payload.get("session_id"),
        "tool_name": payload.get("tool_name"),
        "tool_use_id": payload.get("tool_use_id"),
        "duration_ms": payload.get("duration_ms"),
        "host": "codex",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be a JSON object")
        log_file = (
            resolve_log_file(args.data_dir)
            if args.data_dir
            else resolve_log_file(str(resolve_codex_data_dir()))
        )
        append_event(event_from_hook(payload), log_file)
    except Exception as exc:
        print(f"windows-dev-agent codex trace warning: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
