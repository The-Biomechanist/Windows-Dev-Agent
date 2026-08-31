"""Emit a Codex-valid, session-bound Windows Dev Agent audit summary."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.observability.audit_report import load_events, summarize
from src.observability.trace import resolve_log_file
from src.runtime_paths import resolve_codex_data_dir


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
    log_file = (
        resolve_log_file(args.data_dir)
        if args.data_dir
        else resolve_log_file(str(resolve_codex_data_dir()))
    )
    events = load_events(log_file, session_id=session_id)
    if not events:
        return 0
    summary = summarize(events)
    message = (
        "Windows Dev Agent session audit: "
        f"events={summary['total_events']}, "
        f"execution_succeeded={summary['execution_succeeded']}, "
        f"execution_failed={summary['execution_failed']}, "
        f"execution_unknown={summary['execution_unknown']}, "
        f"not_executed={summary['not_executed']}, "
        f"external_process_started={summary['external_process_started']}, "
        f"permission_denials={summary['permission_denials']}."
    )
    print(json.dumps({"systemMessage": message}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
