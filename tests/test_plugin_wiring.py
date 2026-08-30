"""Tests for installed-plugin path and hook semantics, not just repo execution."""

import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
INSTALLED_PREFIX = "mcp__plugin_windows-dev-agent_windows-dev-agent__"


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


def test_pretool_hook_covers_powershell_and_plugin_scoped_mutations():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    matcher = config["hooks"]["PreToolUse"][0]["matcher"]
    compiled = re.compile(matcher)
    assert compiled.search("PowerShell")
    assert compiled.search("Bash")
    assert compiled.search(INSTALLED_PREFIX + "package_install")
    assert compiled.search(INSTALLED_PREFIX + "sandbox_run")
    assert compiled.search(INSTALLED_PREFIX + "capability_run")


def test_post_hooks_are_scoped_to_windows_dev_agent_mcp_only():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for event in ("PostToolUse", "PostToolUseFailure"):
        matcher = config["hooks"][event][0]["matcher"]
        compiled = re.compile(matcher)
        assert matcher != ".*"
        assert compiled.search(INSTALLED_PREFIX + "env_inspect")
        assert not compiled.search("Read")
        assert not compiled.search("mcp__some-other-server__tool")


def test_stop_hook_does_not_claim_matcher_support():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert "matcher" not in config["hooks"]["Stop"][0]


def test_safety_hook_runs_by_absolute_path_for_installed_scoped_tool(tmp_path: Path):
    gate = ROOT / "src" / "safety" / "gate.py"
    data_dir = tmp_path / "plugin-data"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "session-a",
        "tool_name": INSTALLED_PREFIX + "package_install",
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


def test_read_only_hook_script_emits_no_permission_decision(tmp_path: Path):
    gate = ROOT / "src" / "safety" / "gate.py"
    data_dir = tmp_path / "plugin-data"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "session-a",
        "tool_name": "PowerShell",
        "tool_input": {"command": "Get-ChildItem C:\\src"},
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
    assert result.stdout.strip() == ""


def test_audit_report_filters_to_stop_hook_session(tmp_path: Path):
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    log_file = data_dir / "agent.log"
    log_file.write_text(
        "\n".join(
            [
                json.dumps({"event": "PostToolUse", "session_id": "session-a", "success": True, "tool_name": INSTALLED_PREFIX + "env_inspect"}),
                json.dumps({"event": "PostToolUseFailure", "session_id": "session-b", "success": False, "tool_name": INSTALLED_PREFIX + "package_install"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    audit = ROOT / "src" / "observability" / "audit_report.py"
    report = subprocess.run(
        [sys.executable, str(audit), "--data-dir", str(data_dir)],
        input=json.dumps({"hook_event_name": "Stop", "session_id": "session-a"}),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert report.returncode == 0, report.stderr
    assert "Session Audit" in report.stdout
    assert "Events: 1" in report.stdout
    assert "Execution failures: 0" in report.stdout
    assert "Permission denials: 0" in report.stdout
    assert "package_install" not in report.stdout
