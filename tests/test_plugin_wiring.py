"""Tests for installed-plugin path semantics, not just repo-root execution."""

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def test_manifest_uses_current_schema_surface():
    manifest = json.loads(
        (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["$schema"] == "https://json.schemastore.org/claude-code-plugin-manifest.json"
    assert manifest["name"] == "windows-dev-agent"
    assert manifest["displayName"] == "Windows Dev Agent"
    assert "minClaudeCodeVersion" not in manifest


def test_mcp_server_binds_plugin_data_and_project_roots():
    config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["windows-dev-agent"]
    assert server["command"] == "python"
    assert server["args"] == ["-m", "src.mcp.server"]
    assert server["cwd"] == "${CLAUDE_PLUGIN_ROOT}"
    assert server["env"]["WINDOWS_DEV_AGENT_DATA_DIR"] == "${CLAUDE_PLUGIN_DATA}"
    assert server["env"]["WINDOWS_DEV_AGENT_PROJECT_DIR"] == "${CLAUDE_PROJECT_DIR}"


def test_hook_scripts_are_rooted_and_use_persistent_plugin_data():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    commands = [
        hook["command"]
        for entries in config["hooks"].values()
        for entry in entries
        for hook in entry.get("hooks", [])
        if hook.get("type") == "command"
    ]
    assert commands
    assert all("${CLAUDE_PLUGIN_ROOT}" in command for command in commands)
    assert all("${CLAUDE_PLUGIN_DATA}" in command for command in commands)


def test_safety_hook_runs_by_absolute_path_and_persists_outside_plugin_root(tmp_path: Path):
    gate = ROOT / "src" / "safety" / "gate.py"
    data_dir = tmp_path / "plugin-data"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    event = {
        "hook_event_name": "PreToolUse",
        "tool_name": "mcp__windows-dev-agent__package_install",
        "tool_input": {
            "package_id": "Python.Python.3.12",
            "execute": True,
            "user_approved": True,
        },
    }
    result = subprocess.run(
        [sys.executable, str(gate), "--data-dir", str(data_dir)],
        input=json.dumps(event),
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"
    assert (data_dir / "agent.log").is_file()
    assert not (ROOT / "agent.log").exists()

    audit = ROOT / "src" / "observability" / "audit_report.py"
    report = subprocess.run(
        [sys.executable, str(audit), "--data-dir", str(data_dir)],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert report.returncode == 0, report.stderr
    assert "Events: 1" in report.stdout
