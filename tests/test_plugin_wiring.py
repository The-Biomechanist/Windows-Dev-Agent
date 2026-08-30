"""Tests for installed-plugin path semantics, not just repo-root execution."""

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def test_mcp_server_uses_plugin_root_as_cwd():
    config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["windows-dev-agent"]
    assert server["command"] == "python"
    assert server["args"] == ["-m", "src.mcp.server"]
    assert server["cwd"] == "${CLAUDE_PLUGIN_ROOT}"


def test_hook_scripts_are_rooted_at_plugin_install_directory():
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


def test_safety_hook_runs_by_absolute_script_path_from_arbitrary_cwd(tmp_path: Path):
    gate = ROOT / "src" / "safety" / "gate.py"
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
        [sys.executable, str(gate)],
        input=json.dumps(event),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["permissionDecision"] == "ask"
