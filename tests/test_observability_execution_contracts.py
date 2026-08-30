"""Focused contracts for execution-outcome attribution from MCP tool evidence."""

from src.observability import trace


def _hook(tool_name: str, tool_input: dict, result_text: str | None, event: str = "PostToolUse"):
    response = None
    if result_text is not None:
        response = {"content": [{"type": "text", "text": result_text}]}
    return {
        "hook_event_name": event,
        "session_id": "s1",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_response": response,
    }


def test_package_search_success_is_execution_success_not_not_applicable():
    payload = _hook(
        "mcp__windows-dev-agent__package_search",
        {"query": "Python"},
        '{"status":"completed","succeeded":true,"execution_started":true}',
    )
    assert trace.derive_execution_outcome(payload) == ("succeeded", "completed")


def test_package_search_failure_is_execution_failure():
    payload = _hook(
        "mcp__windows-dev-agent__package_search",
        {"query": "Python"},
        '{"status":"failed","succeeded":false,"execution_started":true}',
    )
    assert trace.derive_execution_outcome(payload) == ("failed", "failed")


def test_prelaunch_failure_is_not_reported_as_execution():
    payload = _hook(
        "mcp__windows-dev-agent__package_search",
        {"query": "Python"},
        '{"status":"failed","succeeded":false,"execution_started":false}',
    )
    assert trace.derive_execution_outcome(payload) == ("not_executed", "failed")


def test_executing_timeout_preserves_unknown_effect_state():
    payload = _hook(
        "mcp__windows-dev-agent__package_install",
        {"execute": True, "package_id": "Python.Python.3.12"},
        '{"status":"failed","succeeded":false,"execution_started":true,"timed_out":true}',
    )
    assert trace.derive_execution_outcome(payload) == ("unknown", "failed")


def test_post_tool_failure_without_execute_flag_is_still_a_tool_failure():
    payload = _hook(
        "mcp__windows-dev-agent__package_search",
        {"query": "Python"},
        None,
        event="PostToolUseFailure",
    )
    assert trace.derive_execution_outcome(payload)[0] == "failed"


def test_nonexecution_status_remains_not_applicable():
    payload = _hook(
        "mcp__windows-dev-agent__mcp_audit",
        {"cwd": "project"},
        '{"status":"issues_found","server_count":2}',
    )
    assert trace.derive_execution_outcome(payload) == ("not_applicable", "issues_found")
