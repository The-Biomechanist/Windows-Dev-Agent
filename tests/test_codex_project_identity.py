"""Focused contracts for unambiguous Codex project identity."""

import asyncio
import json
from pathlib import Path

from src import codex_server
from src.safety import codex_permission

CODEX_PREFIX = "mcp__windows_dev_agent__"


def run(coro):
    return asyncio.run(coro)


def test_codex_project_scoped_call_rejects_relative_project_identity(tmp_path: Path):
    response = run(
        codex_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "workflow_plan",
                    "arguments": {"task": "run tests", "cwd": "."},
                },
            }
        )
    )
    payload = json.loads(response["result"]["content"][0]["text"])

    assert payload["status"] == "invalid_input"
    assert "absolute path" in payload["error"]


def test_permission_hook_does_not_auto_allow_relative_project_identity(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(codex_permission, "append_event", lambda *_args, **_kwargs: None)
    project = tmp_path / "project"
    project.mkdir()

    result = codex_permission.evaluate_permission_request(
        {
            "cwd": str(project),
            "tool_name": CODEX_PREFIX + "workflow_plan",
            "tool_input": {"task": "run tests", "cwd": "."},
        }
    )

    assert result is None


def test_permission_hook_resolves_absolute_descendant_before_auto_allow(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(codex_permission, "append_event", lambda *_args, **_kwargs: None)
    project = tmp_path / "project"
    child = project / "child"
    child.mkdir(parents=True)

    result = codex_permission.evaluate_permission_request(
        {
            "cwd": str(project),
            "tool_name": CODEX_PREFIX + "workflow_plan",
            "tool_input": {"task": "run tests", "cwd": str(child)},
        }
    )

    assert result["hookSpecificOutput"]["decision"]["behavior"] == "allow"
