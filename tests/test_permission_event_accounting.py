"""Permission lifecycle events are control evidence, not execution outcomes."""

from src.safety import codex_gate, codex_permission, gate


CODEX_PREFIX = "mcp__windows_dev_agent__"
CLAUDE_PREFIX = "mcp__plugin_windows-dev-agent_windows-dev-agent__"


def _capture(module, monkeypatch):
    events = []
    monkeypatch.setattr(module, "append_event", lambda event, *_args, **_kwargs: events.append(event))
    return events


def test_claude_pretool_event_is_not_an_execution_attempt(monkeypatch):
    events = _capture(gate, monkeypatch)
    gate.evaluate_hook_event(
        {
            "tool_name": CLAUDE_PREFIX + "package_install",
            "tool_input": {"package_id": "Python.Python.3.12", "execute": True},
        }
    )
    assert events[-1]["execution_outcome"] == "not_applicable"


def test_codex_pretool_event_is_not_an_execution_attempt(monkeypatch):
    events = _capture(codex_gate, monkeypatch)
    codex_gate.evaluate_hook_event(
        {
            "tool_name": CODEX_PREFIX + "package_install",
            "tool_input": {"package_id": "Python.Python.3.12", "execute": True},
        }
    )
    assert events[-1]["execution_outcome"] == "not_applicable"


def test_codex_permission_request_is_not_an_execution_attempt(monkeypatch):
    events = _capture(codex_permission, monkeypatch)
    codex_permission.evaluate_permission_request(
        {
            "tool_name": CODEX_PREFIX + "package_install",
            "tool_input": {"package_id": "Python.Python.3.12", "execute": False},
        }
    )
    assert events[-1]["execution_outcome"] == "not_applicable"
