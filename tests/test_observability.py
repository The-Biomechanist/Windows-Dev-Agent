"""Contracts for minimal, provenance-bound audit state."""

import json
from pathlib import Path

from src.observability import audit_report, trace


def _hook(tool_input, tool_response=None, event="PostToolUse"):
    return {
        "hook_event_name": event,
        "session_id": "s1",
        "tool_name": "mcp__windows-dev-agent__package_install",
        "tool_use_id": "u1",
        "tool_input": tool_input,
        "tool_response": tool_response,
    }


def test_trace_persists_derived_metadata_not_payloads():
    event = trace.event_from_hook(
        _hook(
            {"execute": True, "package_id": "secret-value"},
            {"content": [{"type": "text", "text": json.dumps({"status": "completed", "succeeded": True, "stdout": "another-secret"})}]},
        )
    )
    assert event["execution_outcome"] == "succeeded"
    assert event["result_status"] == "completed"
    serialized = json.dumps(event)
    assert "tool_input" not in event and "tool_response" not in event
    assert "secret-value" not in serialized
    assert "another-secret" not in serialized


def test_execution_outcome_preserves_plan_success_failure_launch_and_unknown():
    assert trace.derive_execution_outcome(_hook({"execute": False}))[0] == "not_executed"
    assert trace.derive_execution_outcome(
        _hook({"execute": True}, {"content": [{"type": "text", "text": '{"status":"completed","succeeded":true}'}]})
    )[0] == "succeeded"
    assert trace.derive_execution_outcome(
        _hook({"execute": True}, {"content": [{"type": "text", "text": '{"status":"completed","succeeded":false}'}]})
    )[0] == "failed"
    assert trace.derive_execution_outcome(
        _hook({"execute": True}, {"content": [{"type": "text", "text": '{"status":"launched"}'}]})
    )[0] == "unknown"
    assert trace.derive_execution_outcome(_hook({"execute": True}, None))[0] == "unknown"


def test_session_filter_never_launders_other_session_events(tmp_path: Path):
    log = tmp_path / "agent.log"
    log.write_text(
        "\n".join(
            [
                json.dumps({"session_id": "a", "event": "PostToolUse", "execution_outcome": "unknown", "tool_name": "one"}),
                json.dumps({"session_id": "b", "event": "PostToolUse", "execution_outcome": "failed", "tool_name": "two"}),
            ]
        ) + "\n",
        encoding="utf-8",
    )
    events = audit_report.load_events(log, session_id="a")
    summary = audit_report.summarize(events)
    assert len(events) == 1
    assert summary["execution_failed"] == 0
    assert summary["execution_unknown"] == 1


def test_permission_event_is_not_counted_as_execution_attempt():
    summary = audit_report.summarize(
        [{
            "event": "PreToolUse",
            "execution_outcome": "not_applicable",
            "permission_denied": True,
            "permission_decision": "deny",
            "tool_name": "PowerShell",
        }]
    )
    assert summary["execution_failed"] == 0
    assert summary["execution_unknown"] == 0
    assert summary["not_executed"] == 0
    assert summary["not_applicable"] == 1
    assert summary["permission_denials"] == 1


def test_log_rotation_bounds_persistent_history(tmp_path: Path, monkeypatch):
    log = tmp_path / "agent.log"
    monkeypatch.setattr(trace, "MAX_LOG_BYTES", 30)
    trace.append_event({"one": "x" * 40}, log)
    trace.append_event({"two": "y"}, log)
    assert log.is_file()
    assert log.with_name("agent.log.1").is_file()
    assert "one" in log.with_name("agent.log.1").read_text(encoding="utf-8")
    assert "two" in log.read_text(encoding="utf-8")


def test_unfiltered_audit_is_explicit_history_surface(tmp_path: Path):
    log = tmp_path / "agent.log"
    log.write_text(json.dumps({"session_id": "a", "event": "PostToolUse", "execution_outcome": "unknown"}) + "\n", encoding="utf-8")
    assert len(audit_report.load_events(log)) == 1
