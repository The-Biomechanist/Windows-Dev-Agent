"""Installed Claude plugin wiring and hook semantics."""

import json
from pathlib import Path
import re
import subprocess
import sys

from src import __version__

ROOT = Path(__file__).resolve().parent.parent
INSTALLED_PREFIX = "mcp__plugin_windows-dev-agent_windows-dev-agent__"


def test_manifest_and_runtime_version_match_release():
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["$schema"] == "https://json.schemastore.org/claude-code-plugin-manifest.json"
    assert manifest["name"] == "windows-dev-agent"
    assert manifest["displayName"] == "Windows Dev Agent"
    assert manifest["version"] == __version__ == "0.4.1"
    assert "minClaudeCodeVersion" not in manifest


def test_mcp_server_binds_plugin_data_and_project_roots():
    config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    server = config["mcpServers"]["windows-dev-agent"]
    assert server["command"] == "python"
    assert server["args"] == ["-m", "src.claude_server"]
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


def test_pretool_hook_covers_shell_mutations_and_broader_mcp_reads():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    matcher = re.compile(config["hooks"]["PreToolUse"][0]["matcher"])
    assert matcher.search("PowerShell")
    assert matcher.search("Bash")
    for name in ("package_install", "sandbox_run", "capability_run", "ecosystem_scan", "mcp_audit"):
        assert matcher.search(INSTALLED_PREFIX + name)


def test_post_hooks_are_scoped_to_windows_dev_agent_mcp_only():
    config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for event in ("PostToolUse", "PostToolUseFailure"):
        matcher = re.compile(config["hooks"][event][0]["matcher"])
        assert matcher.search(INSTALLED_PREFIX + "env_inspect")
        assert not matcher.search("Read")
        assert not matcher.search("mcp__some-other-server__tool")


def test_safety_hook_asks_for_exact_execute_call_without_fake_acknowledgement(tmp_path: Path):
    gate = ROOT / "src" / "safety" / "gate.py"
    data_dir = tmp_path / "plugin-data"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "session-a",
        "tool_name": INSTALLED_PREFIX + "package_install",
        "tool_input": {"package_id": "Python.Python.3.12", "execute": True},
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


def test_read_only_hook_emits_no_permission_decision(tmp_path: Path):
    gate = ROOT / "src" / "safety" / "gate.py"
    data_dir = tmp_path / "plugin-data"
    event = {
        "hook_event_name": "PreToolUse",
        "session_id": "session-a",
        "tool_name": "PowerShell",
        "tool_input": {"command": "Get-ChildItem C:\\src"},
    }
    result = subprocess.run(
        [sys.executable, str(gate), "--data-dir", str(data_dir)],
        input=json.dumps(event),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ""


def test_audit_report_filters_to_current_session_and_preserves_unknown(tmp_path: Path):
    data_dir = tmp_path / "plugin-data"
    data_dir.mkdir()
    log_file = data_dir / "agent.log"
    log_file.write_text(
        "\n".join(
            [
                json.dumps({"event": "PostToolUse", "session_id": "session-a", "execution_outcome": "unknown", "tool_name": INSTALLED_PREFIX + "sandbox_run"}),
                json.dumps({"event": "PostToolUse", "session_id": "session-b", "execution_outcome": "failed", "tool_name": INSTALLED_PREFIX + "package_install"}),
            ]
        ) + "\n",
        encoding="utf-8",
    )
    audit = ROOT / "src" / "observability" / "audit_report.py"
    report = subprocess.run(
        [sys.executable, str(audit), "--data-dir", str(data_dir)],
        input=json.dumps({"hook_event_name": "Stop", "session_id": "session-a"}),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert report.returncode == 0, report.stderr
    assert "Session Audit" in report.stdout
    assert "Events: 1" in report.stdout
    assert "failed: 0" in report.stdout
    assert "unknown: 1" in report.stdout
    assert "package_install" not in report.stdout
