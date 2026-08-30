"""Minimal structured audit logger for Windows Dev Agent hook events.

Persistent audit state intentionally records only the metadata consumed by the
plugin's audit surfaces. Raw tool inputs, command bodies, stdout/stderr, and
arbitrary tool responses are not persisted here.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR = ROOT / ".cache"


def resolve_log_file(data_dir: Optional[str] = None) -> Path:
    configured = data_dir or os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR")
    directory = Path(configured).expanduser() if configured else DEFAULT_DATA_DIR
    return directory / "agent.log"


def event_from_hook(payload: dict[str, Any]) -> dict[str, Any]:
    hook_event = str(payload.get("hook_event_name", "unknown"))
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": hook_event,
        "success": hook_event != "PostToolUseFailure",
        "session_id": payload.get("session_id"),
        "agent_id": payload.get("agent_id"),
        "agent_type": payload.get("agent_type"),
        "tool_name": payload.get("tool_name"),
        "tool_use_id": payload.get("tool_use_id"),
        "duration_ms": payload.get("duration_ms"),
        "error_present": bool(payload.get("error")),
    }


def append_event(event: dict[str, Any], log_file: Optional[Path] = None) -> None:
    target = log_file or resolve_log_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be a JSON object")
        append_event(event_from_hook(payload), resolve_log_file(args.data_dir))
    except Exception as exc:
        print(f"windows-dev-agent trace warning: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
