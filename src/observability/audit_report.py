"""Concise Windows Dev Agent audit summaries with explicit unknown outcomes."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Optional

try:
    from .trace import resolve_log_file
except ImportError:
    from trace import resolve_log_file


def load_events(
    log_file: Optional[Path] = None,
    *,
    session_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    target = log_file or resolve_log_file()
    if not target.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(value, dict):
            continue
        if session_id is not None and value.get("session_id") != session_id:
            continue
        events.append(value)
    return events


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    event_types = Counter(str(event.get("event", "unknown")) for event in events)
    tools = Counter(str(event.get("tool_name", "unknown")) for event in events if event.get("tool_name"))
    outcomes = Counter(
        str(event.get("execution_outcome"))
        for event in events
        if event.get("execution_outcome") in {"succeeded", "failed", "unknown", "not_executed", "not_applicable"}
    )
    # Backward compatibility for old log entries that predate execution_outcome.
    legacy_failures = [
        event for event in events
        if "execution_outcome" not in event and event.get("success") is False
    ]
    denied = [event for event in events if event.get("permission_denied") is True]
    failed_tools = [
        event for event in events
        if event.get("execution_outcome") == "failed"
        or ("execution_outcome" not in event and event.get("success") is False)
    ]
    return {
        "total_events": len(events),
        "event_types": dict(event_types),
        "tools": dict(tools),
        "execution_succeeded": outcomes.get("succeeded", 0),
        "execution_failed": outcomes.get("failed", 0) + len(legacy_failures),
        "execution_unknown": outcomes.get("unknown", 0),
        "not_executed": outcomes.get("not_executed", 0),
        "not_applicable": outcomes.get("not_applicable", 0),
        "permission_denials": len(denied),
        "last_failure_tool": failed_tools[-1].get("tool_name") if failed_tools else None,
        "last_denied_tool": denied[-1].get("tool_name") if denied else None,
    }


def _hook_session_id() -> Optional[str]:
    try:
        raw = sys.stdin.read().strip()
    except Exception:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("session_id")
    return str(value) if value else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--session-id", default=None)
    args = parser.parse_args()

    session_id = args.session_id or _hook_session_id()
    events = load_events(resolve_log_file(args.data_dir), session_id=session_id)
    if not events:
        print(
            "Windows Dev Agent audit: no Windows Dev Agent events recorded in this session."
            if session_id
            else "Windows Dev Agent audit: no persistent audit events recorded."
        )
        return 0

    summary = summarize(events)
    title = "Session Audit" if session_id else "Persistent Audit History"
    print(f"\n=== Windows Dev Agent — {title} ===")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(
        f"Events: {summary['total_events']} | "
        f"Execution succeeded: {summary['execution_succeeded']} | "
        f"failed: {summary['execution_failed']} | "
        f"unknown: {summary['execution_unknown']} | "
        f"not executed: {summary['not_executed']} | "
        f"Permission denials: {summary['permission_denials']}"
    )
    if summary["tools"]:
        print("Tools:")
        for name, count in sorted(summary["tools"].items()):
            print(f"  {name}: {count}")
    if summary["last_failure_tool"]:
        print(f"Last known failed execution: {summary['last_failure_tool']}")
    if summary["last_denied_tool"]:
        print(f"Last denied request: {summary['last_denied_tool']}")
    print("=" * 43)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
