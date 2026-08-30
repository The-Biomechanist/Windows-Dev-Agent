"""Contract tests for the Codex adapter over the shared Windows Dev Agent core."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from src import __version__, codex_server
from src.mcp import server as common_server
from src.observability import codex_trace
from src.runtime_paths import resolve_codex_data_dir
from src.safety import codex_gate, codex_permission

ROOT = Path(__file__).resolve().parent.parent
CODEX_PREFIX = "mcp__windows_dev_agent__"


def run(coro):
    return asyncio.run(coro)


def test_codex_manifest_points_to_shared_root_components_and_version():
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "windows-dev-agent"
    assert manifest["version"] == __version__ == "0.5.0"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.codex.json"
    assert manifest["hooks"] == "./hooks/codex-hooks.json"
    assert "ChatGPT desktop" not in manifest["description"]


def test_marketplace_index_when_present_is_immutable_distribution_metadata():
    path = ROOT / ".agents" / "plugins" / "marketplace.json"
    if not path.exists():
        return
    market = json.loads(path.read_text(encoding="utf-8"))
    plugin = market["plugins"][0]
    source = plugin["source"]
    assert plugin["name"] == "windows-dev-agent"
    assert source["source"] == "url"
    assert source["url"].endswith("/The-Biomechanist/Windows-Dev-Agent.git")
    assert re.fullmatch(r"[0-9a-f]{40}", source["sha"])
    assert "ref" not in source and "path" not in source


def test_codex_mcp_uses_native_plugin_cwd_for_isolated_launcher():
    raw = (ROOT / ".mcp.codex.json").read_text(encoding="utf-8")
    config = json.loads(raw)
    assert list(config) == ["windows_dev_agent"]
    server = config["windows_dev_agent"]
    assert server["command"] == "powershell.exe"
    assert server["cwd"] == "."
    assert "scripts/launch-python.ps1" in server["args"]
    assert "${PLUGIN_ROOT}" not in raw
    assert server["args"][-2:] == ["-Module", "src.codex_server"]
    assert server["tool_timeout_sec"] > 600
    assert server["default_tools_approval_mode"] == "prompt"
    for tool in ("env_inspect", "logs_query"):
        assert server["tools"][tool]["approval_mode"] == "approve"
    for tool in (
        "tool_discover", "workflow_plan", "package_search", "ecosystem_scan", "mcp_audit",
        "capability_run", "package_install", "sandbox_run",
    ):
        assert server["tools"][tool]["approval_mode"] == "prompt"


def test_all_shared_skills_have_codex_frontmatter_names():
    skill_dirs = sorted(path for path in (ROOT / "skills").iterdir() if path.is_dir())
    assert {path.name for path in skill_dirs} == {
        "ecosystem-defrag", "env-inspect", "package-install", "sandbox-run", "win-setup", "workflow-plan",
    }
    for directory in skill_dirs:
        text = (directory / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        assert f"\nname: {directory.name}\n" in f"\n{frontmatter}\n"
        assert "\ndescription:" in f"\n{frontmatter}\n"


def test_codex_project_scoped_tools_require_explicit_project_identity():
    tools = {tool["name"]: tool for tool in codex_server.TOOLS}
    for name, argument in codex_server.PROJECT_ARG_BY_TOOL.items():
        schema = tools[name]["inputSchema"]
        assert argument in schema["properties"]
        assert argument in schema["required"]


def test_codex_rejects_project_scoped_call_without_current_project():
    response = run(
        codex_server.handle_request(
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "workflow_plan", "arguments": {"task": "run tests"}}}
        )
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["status"] == "invalid_input"
    assert "project directory" in payload["error"]


def test_codex_initialize_explains_project_permission_and_hook_trust_boundaries():
    response = run(codex_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))
    instructions = response["result"]["instructions"]
    assert "project directory" in instructions
    assert "approval" in instructions
    assert "trust" in instructions
    assert response["result"]["serverInfo"] == {"name": "windows-dev-agent", "version": __version__}


def test_runtime_binding_restores_shared_core_state(monkeypatch, tmp_path: Path):
    before = (common_server.DATA_DIR, common_server.LOG_FILE)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    project = tmp_path / "project"
    project.mkdir()
    with codex_server._runtime_binding(project):
        assert common_server.DATA_DIR == resolve_codex_data_dir()
        assert os.environ["WINDOWS_DEV_AGENT_PROJECT_DIR"] == str(project)
    assert (common_server.DATA_DIR, common_server.LOG_FILE) == before


def _ecosystem_response(host_included: bool):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps({"status": "ok", "inventory": {"host_inventory_included": host_included}})}]},
    }


def test_codex_ecosystem_augmentation_only_adds_host_inventory_when_requested(monkeypatch):
    monkeypatch.setattr(codex_server, "_codex_plugin_inventory", lambda: {"personal": ["one"]})
    bounded = codex_server._augment_ecosystem_response(_ecosystem_response(False))
    bounded_payload = json.loads(bounded["result"]["content"][0]["text"])
    assert "codex_plugins" not in bounded_payload["inventory"]

    broad = codex_server._augment_ecosystem_response(_ecosystem_response(True))
    broad_payload = json.loads(broad["result"]["content"][0]["text"])
    assert broad_payload["inventory"]["codex_plugins"]["personal"] == ["one"]


def test_codex_gate_denies_forbidden_and_otherwise_defers_to_native_policy(monkeypatch):
    monkeypatch.setattr(codex_gate, "append_event", lambda *_args, **_kwargs: None)
    assert codex_gate.evaluate_hook_event({"tool_name": "Bash", "tool_input": {"command": "git status --short"}}) is None
    assert codex_gate.evaluate_hook_event({"tool_name": CODEX_PREFIX + "package_install", "tool_input": {"package_id": "Python.Python.3.12", "execute": True}}) is None
    forbidden = codex_gate.evaluate_hook_event({"tool_name": "Bash", "tool_input": {"command": "format C:"}})
    assert forbidden["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_codex_permission_request_auto_allows_only_scope_proven_plans(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(codex_permission, "append_event", lambda *_args, **_kwargs: None)
    project = tmp_path / "project"
    child = project / "child"
    outside = tmp_path / "outside"
    child.mkdir(parents=True)
    outside.mkdir()

    package = CODEX_PREFIX + "package_install"
    planned_package = codex_permission.evaluate_permission_request(
        {"cwd": str(project), "tool_name": package, "tool_input": {"package_id": "Python.Python.3.12", "execute": False}}
    )
    assert planned_package["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    assert codex_permission.evaluate_permission_request(
        {"cwd": str(project), "tool_name": package, "tool_input": {"package_id": "Python.Python.3.12", "execute": True}}
    ) is None

    capability = CODEX_PREFIX + "capability_run"
    planned_capability = codex_permission.evaluate_permission_request(
        {"cwd": str(project), "tool_name": capability, "tool_input": {"capability": "test-python", "cwd": str(child), "execute": False}}
    )
    assert planned_capability["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    assert codex_permission.evaluate_permission_request(
        {"cwd": str(project), "tool_name": capability, "tool_input": {"capability": "test-python", "cwd": str(outside), "execute": False}}
    ) is None

    workflow = CODEX_PREFIX + "workflow_plan"
    planned_workflow = codex_permission.evaluate_permission_request(
        {"cwd": str(project), "tool_name": workflow, "tool_input": {"task": "run tests", "cwd": str(project)}}
    )
    assert planned_workflow["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    assert codex_permission.evaluate_permission_request(
        {"cwd": str(project), "tool_name": workflow, "tool_input": {"task": "run tests", "cwd": str(outside)}}
    ) is None

    sandbox = CODEX_PREFIX + "sandbox_run"
    assert codex_permission.evaluate_permission_request(
        {"cwd": str(project), "tool_name": sandbox, "tool_input": {"command": "pwd", "workspace_folder": str(project), "execute": False}}
    ) is None

    for name, tool_input in (
        ("ecosystem_scan", {"cwd": str(project), "include_host": False}),
        ("mcp_audit", {"cwd": str(project), "include_host": False}),
        ("tool_discover", {"category": "runtimes"}),
        ("package_search", {"query": "Python"}),
    ):
        assert codex_permission.evaluate_permission_request({"cwd": str(project), "tool_name": CODEX_PREFIX + name, "tool_input": tool_input}) is None


def test_codex_hook_config_avoids_windows_quoted_command_bug_and_sandbox_autoallow():
    raw = (ROOT / "hooks" / "codex-hooks.json").read_text(encoding="utf-8")
    config = json.loads(raw)
    assert "permissionDecision: ask" not in raw
    assert "windows_dev_agent" in config["hooks"]["PreToolUse"][0]["matcher"]
    permission_matcher = config["hooks"]["PermissionRequest"][0]["matcher"]
    assert "capability_run" in permission_matcher
    assert "workflow_plan" in permission_matcher
    assert "package_install" in permission_matcher
    assert "sandbox_run" not in permission_matcher
    assert "ecosystem_scan" not in permission_matcher
    assert "mcp_audit" not in permission_matcher
    assert "tool_discover" not in permission_matcher
    assert "package_search" not in permission_matcher
    commands = [
        hook["commandWindows"]
        for entries in config["hooks"].values()
        for entry in entries
        for hook in entry.get("hooks", [])
    ]
    assert commands
    assert all('"' not in command for command in commands)
    assert all("$env:PLUGIN_ROOT" in command for command in commands)
    assert all("launch-python.ps1" in command for command in commands)


def test_codex_trace_derives_known_success_and_preserves_unknown_without_payload():
    success = codex_trace.event_from_hook(
        {
            "session_id": "s1",
            "tool_name": CODEX_PREFIX + "package_install",
            "tool_input": {"execute": True, "package_id": "secret"},
            "tool_response": {"content": [{"type": "text", "text": '{"status":"completed","succeeded":true,"stdout":"secret"}'}]},
        }
    )
    assert success["execution_outcome"] == "succeeded"
    assert "secret" not in json.dumps(success)

    launched = codex_trace.event_from_hook(
        {
            "session_id": "s1",
            "tool_name": CODEX_PREFIX + "sandbox_run",
            "tool_input": {"execute": True},
            "tool_response": {"content": [{"type": "text", "text": '{"status":"launched"}'}]},
        }
    )
    assert launched["execution_outcome"] == "unknown"


def test_codex_stop_hook_reports_unknown_instead_of_zero_failures(tmp_path: Path):
    codex_home = tmp_path / "codex-home"
    data_dir = codex_home / "plugins" / "data" / "windows-dev-agent"
    data_dir.mkdir(parents=True)
    (data_dir / "agent.log").write_text(
        json.dumps({
            "event": "PostToolUse",
            "execution_outcome": "unknown",
            "session_id": "session-codex",
            "tool_name": CODEX_PREFIX + "sandbox_run",
            "host": "codex",
        }) + "\n",
        encoding="utf-8",
    )
    script = ROOT / "src" / "observability" / "codex_audit_report.py"
    env = dict(os.environ)
    env.pop("WINDOWS_DEV_AGENT_DATA_DIR", None)
    env["CODEX_HOME"] = str(codex_home)
    result = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps({"hook_event_name": "Stop", "session_id": "session-codex"}),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert "execution_failed=0" in output["systemMessage"]
    assert "execution_unknown=1" in output["systemMessage"]


def test_codex_data_dir_uses_codex_home_not_hook_only_plugin_data(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("WINDOWS_DEV_AGENT_DATA_DIR", raising=False)
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "hook-only"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    assert resolve_codex_data_dir() == tmp_path / "codex-home" / "plugins" / "data" / "windows-dev-agent"
