"""Emit a Codex-valid Stop-hook audit summary for the current session."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

try:
    from .audit_report import load_events, summarize
    from .trace import resolve_log_file
except ImportError:
    from audit_report import load_events, summarize
    from trace import resolve_log_file


def _session_id(payload: dict[str, Any]) -> Optional[str]:
    value = payload.get("session_id")
    return str(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args()

    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook payload must be a JSON object")
    except Exception:
        return 0

    session_id = _session_id(payload)
    if not session_id:
        return 0

    events = load_events(
        resolve_log_file(args.data_dir),
        session_id=session_id,
    )
    if not events:
        return 0

    summary = summarize(events)
    message = (
        "Windows Dev Agent session audit: "
        f"events={summary['total_events']}, "
        f"execution_failures={summary['failures']}, "
        f"permission_denials={summary.get('permission_denials', 0)}."
    )
    print(json.dumps({"systemMessage": message}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
