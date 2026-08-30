"""Contract tests for minimal, provenance-bound audit state."""

import json
from pathlib import Path

from src.observability import audit_report, trace


def test_trace_persists_metadata_not_tool_payloads():
    event = trace.event_from_hook(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "tool_name": "mcp__plugin_windows-dev-agent_windows-dev-agent__package_search",
            "tool_use_id": "u1",
            "tool_input": {"query": "secret-value", "TOKEN": "definitely-secret"},
            "tool_response": {"stdout": "another-secret"},
        }
    )
    assert event["session_id"] == "s1"
    assert event["tool_name"].endswith("package_search")
    assert "tool_input" not in event
    assert "tool_response" not in event
    assert "secret-value" not in json.dumps(event)
    assert "another-secret" not in json.dumps(event)


def test_session_filter_never_launders_other_session_events(tmp_path: Path):
    log = tmp_path / "agent.log"
    log.write_text(
        "\n".join(
            [
                json.dumps({"session_id": "a", "event": "PostToolUse", "success": True, "tool_name": "one"}),
                json.dumps({"session_id": "b", "event": "PostToolUseFailure", "success": False, "tool_name": "two"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    events = audit_report.load_events(log, session_id="a")
    assert len(events) == 1
    assert events[0]["tool_name"] == "one"
    assert audit_report.summarize(events)["failures"] == 0


def test_unfiltered_audit_is_explicitly_history_surface(tmp_path: Path):
    log = tmp_path / "agent.log"
    log.write_text(json.dumps({"session_id": "a", "event": "PostToolUse", "success": True}) + "\n", encoding="utf-8")
    assert len(audit_report.load_events(log)) == 1
