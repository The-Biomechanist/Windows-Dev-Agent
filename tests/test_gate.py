"""Tests for the Claude Code PreToolUse safety gate."""

from src.safety import gate


def test_read_only_bash_is_allowed():
    assert gate.classify_bash("git status --short") == "read-only"


def test_unknown_bash_asks_instead_of_default_allow():
    assert gate.classify_bash("some-new-tool mutate-things") == "approval-required"


def test_package_install_bash_requires_approval():
    assert gate.classify_bash("winget install --id Python.Python.3.12 --exact") == "approval-required"


def test_destructive_disk_command_is_forbidden():
    assert gate.classify_bash("format C:") == "forbidden"


def test_mcp_plan_is_allowed_without_execution(monkeypatch):
    monkeypatch.setattr(gate, "append_event", lambda *_args, **_kwargs: None)
    event = {
        "tool_name": "mcp__windows-dev-agent__package_install",
        "tool_input": {"package_id": "Python.Python.3.12", "execute": False},
    }
    output = gate.evaluate_hook_event(event)
    assert output["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_mcp_execution_forces_host_prompt(monkeypatch):
    monkeypatch.setattr(gate, "append_event", lambda *_args, **_kwargs: None)
    event = {
        "tool_name": "mcp__windows-dev-agent__package_install",
        "tool_input": {"package_id": "Python.Python.3.12", "execute": True, "user_approved": True},
    }
    output = gate.evaluate_hook_event(event)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"


def test_approval_required_capability_forces_host_prompt(monkeypatch):
    monkeypatch.setattr(gate, "append_event", lambda *_args, **_kwargs: None)
    event = {
        "tool_name": "mcp__windows-dev-agent__capability_run",
        "tool_input": {"capability": "create-pr", "execute": True, "user_approved": True},
    }
    output = gate.evaluate_hook_event(event)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"
