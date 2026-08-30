"""Contract tests for the Claude Code adapter boundary over the shared runtime."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src import claude_server


def run(coro):
    return asyncio.run(coro)


def _call(name: str, arguments: dict):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _payload(response):
    return json.loads(response["result"]["content"][0]["text"])


def test_project_scoped_call_defaults_to_host_project(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("WINDOWS_DEV_AGENT_PROJECT_DIR", str(project))

    normalized, error = claude_server._bind_project_scope(
        _call("workflow_plan", {"task": "run tests"})
    )

    assert error is None
    assert normalized["params"]["arguments"]["cwd"] == str(project.resolve())


def test_project_scoped_call_accepts_descendant(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    child = project / "subproject"
    child.mkdir(parents=True)
    monkeypatch.setenv("WINDOWS_DEV_AGENT_PROJECT_DIR", str(project))

    normalized, error = claude_server._bind_project_scope(
        _call("capability_run", {"capability": "test-python", "cwd": str(child)})
    )

    assert error is None
    assert normalized["params"]["arguments"]["cwd"] == str(child.resolve())


def test_project_scoped_call_rejects_directory_outside_host_project(monkeypatch, tmp_path: Path):
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    monkeypatch.setenv("WINDOWS_DEV_AGENT_PROJECT_DIR", str(project))

    response = run(
        claude_server.handle_request(
            _call(
                "sandbox_run",
                {"command": "pwd", "workspace_folder": str(outside), "environment": "wsl"},
            )
        )
    )
    payload = _payload(response)

    assert payload["status"] == "invalid_input"
    assert "active Claude project" in payload["error"]


def test_project_scoped_call_fails_closed_without_host_project(monkeypatch):
    monkeypatch.delenv("WINDOWS_DEV_AGENT_PROJECT_DIR", raising=False)

    response = run(
        claude_server.handle_request(
            _call("ecosystem_scan", {"include_host": False})
        )
    )
    payload = _payload(response)

    assert payload["status"] == "invalid_input"
    assert "not supplied by the host" in payload["error"]


def test_nonproject_tool_does_not_require_project_binding(monkeypatch):
    monkeypatch.delenv("WINDOWS_DEV_AGENT_PROJECT_DIR", raising=False)
    request = _call("package_install", {"package_id": "Python.Python.3.12"})

    normalized, error = claude_server._bind_project_scope(request)

    assert error is None
    assert normalized == request
