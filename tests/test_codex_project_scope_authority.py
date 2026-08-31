"""Codex trusted hooks must bind project-scoped calls to the host event cwd."""

from __future__ import annotations

import json
from pathlib import Path

from src.safety import codex_gate, codex_permission


ROOT = Path(__file__).resolve().parent.parent
PREFIX = "mcp__windows_dev_agent__"


def _event(project: Path, tool: str, tool_input: dict) -> dict:
    return {
        "cwd": str(project),
        "session_id": "scope-session",
        "tool_name": PREFIX + tool,
        "tool_input": tool_input,
    }


def test_pretool_gate_denies_every_project_scoped_escape(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(codex_gate, "append_event", lambda *_args, **_kwargs: None)
    project = tmp_path / "project"
    child = project / "child"
    outside = tmp_path / "outside"
    child.mkdir(parents=True)
    outside.mkdir()

    calls = (
        ("capability_run", "cwd", {"capability": "test-python", "execute": True}),
        ("workflow_plan", "cwd", {"task": "run tests"}),
        ("sandbox_run", "workspace_folder", {"command": "pwd", "isolation_requirement": "linux_compatibility", "execute": True}),
        ("ecosystem_scan", "cwd", {"include_host": False}),
        ("mcp_audit", "cwd", {"include_host": False}),
    )
    for tool, argument, base in calls:
        inside = codex_gate.evaluate_hook_event(_event(project, tool, {**base, argument: str(child)}))
        assert inside is None, tool

        escaped = codex_gate.evaluate_hook_event(_event(project, tool, {**base, argument: str(outside)}))
        assert escaped is not None, tool
        output = escaped["hookSpecificOutput"]
        assert output["permissionDecision"] == "deny"
        assert "project" in output["permissionDecisionReason"].lower()


def test_pretool_gate_fails_closed_when_host_cwd_or_project_argument_is_unestablished(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(codex_gate, "append_event", lambda *_args, **_kwargs: None)
    project = tmp_path / "project"
    project.mkdir()

    missing_host = codex_gate.evaluate_hook_event(
        {"tool_name": PREFIX + "ecosystem_scan", "tool_input": {"cwd": str(project), "include_host": False}}
    )
    assert missing_host is not None
    assert missing_host["hookSpecificOutput"]["permissionDecision"] == "deny"

    missing_argument = codex_gate.evaluate_hook_event(
        {"cwd": str(project), "tool_name": PREFIX + "mcp_audit", "tool_input": {"include_host": False}}
    )
    assert missing_argument is not None
    assert missing_argument["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_permission_plan_autoallow_reuses_pretool_scope_authority(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(codex_permission, "append_event", lambda *_args, **_kwargs: None)
    project = tmp_path / "project"
    child = project / "child"
    outside = tmp_path / "outside"
    child.mkdir(parents=True)
    outside.mkdir()

    allowed = codex_permission.evaluate_permission_request(
        _event(project, "workflow_plan", {"task": "run tests", "cwd": str(child)})
    )
    assert allowed is not None
    assert allowed["hookSpecificOutput"]["decision"]["behavior"] == "allow"

    assert codex_permission.evaluate_permission_request(
        _event(project, "workflow_plan", {"task": "run tests", "cwd": str(outside)})
    ) is None


def test_codex_pretool_matcher_covers_all_project_scoped_tools():
    config = json.loads((ROOT / "hooks" / "codex-hooks.json").read_text(encoding="utf-8"))
    matcher = config["hooks"]["PreToolUse"][0]["matcher"]
    for tool in ("capability_run", "workflow_plan", "sandbox_run", "ecosystem_scan", "mcp_audit"):
        assert tool in matcher
