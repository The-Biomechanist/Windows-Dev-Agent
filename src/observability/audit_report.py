"""Print a concise audit summary from the structured JSONL session log."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
LOG_FILE = ROOT / "agent.log"


def load_events(log_file: Path = LOG_FILE) -> list[dict[str, Any]]:
    if not log_file.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in log_file.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    event_types = Counter(str(event.get("event", "unknown")) for event in events)
    tools = Counter(str(event.get("tool_name", "unknown")) for event in events if event.get("tool_name"))
    failures = [event for event in events if event.get("success") is False]
    return {
        "total_events": len(events),
        "event_types": dict(event_types),
        "tools": dict(tools),
        "failures": len(failures),
        "last_failure_tool": failures[-1].get("tool_name") if failures else None,
    }


def main() -> int:
    events = load_events()
    if not events:
        print("Windows Dev Agent audit: no session events recorded.")
        return 0

    summary = summarize(events)
    print("\n=== Windows Dev Agent — Session Audit ===")
    print(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Events: {summary['total_events']} | Failures: {summary['failures']}")
    if summary["tools"]:
        print("Tools:")
        for name, count in sorted(summary["tools"].items()):
            print(f"  {name}: {count}")
    if summary["last_failure_tool"]:
        print(f"Last failed tool: {summary['last_failure_tool']}")
    print("=" * 43)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
