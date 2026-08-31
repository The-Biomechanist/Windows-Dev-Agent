"""Codex PostToolUse audit adapter.

Codex does not provide Claude Code's separate PostToolUseFailure event, so this
adapter inspects the Windows Dev Agent MCP result in memory and persists only a
small derived execution outcome/result status. Raw payloads are discarded.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.observability.trace import (
    append_event,
    derive_execution_outcome,
    derive_execution_started,
    resolve_log_file,
)
from src.runtime_paths import resolve_codex_data_dir


def event_from_hook(payload: dict[str, Any]) -> dict[str, Any]:
    execution_outcome, result_status = derive_execution_outcome(payload)
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "PostToolUse",
        "success": None,
        "execution_started": derive_execution_started(payload),
        "execution_outcome": execution_outcome,
        "result_status": result_status,
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
