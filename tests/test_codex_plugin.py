"""Contract tests for the Codex adapter over the shared Windows Dev Agent core."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from src import __version__, codex_server
from src.mcp import server as common
from src.observability import codex_trace
from src.runtime_paths import resolve_codex_data_dir
from src.safety import codex_gate, codex_permission

ROOT = Path(__file__).resolve().parent.parent
CODEX_PREFIX = "mcp__windows_dev_agent__"


def run(coro):
    return asyncio.run(coro)


def test_codex_manifest_points_to_shared_root_components():
    manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "windows-dev-agent"
    assert manifest["version"] == __version__ == "0.3.0"
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.codex.json"
    assert manifest["hooks"] == "./hooks/codex-hooks.json"


def test_repo_marketplace_installs_plugin_from_repository_root_without_local_root_path():
    market = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = market["plugins"][0]
    assert plugin["name"] == "windows-dev-agent"
    assert plugin["source"]["source"] == "url"
    assert plugin["source"]["url"].endswith("/The-Biomechanist/Windows-Dev-Agent.git")
    assert plugin["source"]["ref"] == "main"
    assert "path" not in plugin["source"]


def test_codex_mcp_uses_callable_namespace_and_native_approval_policy():
    config = json.loads((ROOT / ".mcp.codex.json").read_text(encoding="utf-8"))
    assert list(config) == ["windows_dev_agent"]
    server = config["windows_dev_agent"]
    assert server["args"] == ["-m", "src.codex_server"]
    assert server["cwd"] == "."
    assert server["default_tools_approval_mode"] == "prompt"

    for tool in (
        "env_inspect",
        "tool_discover",
        "workflow_plan",
        "package_search",
        "ecosystem_scan",
        "logs_query",
        "mcp_audit",
    ):
        assert server["tools"][tool]["approval_mode"] == "approve"
    for tool in ("capability_run", "package_install", "sandbox_run"):
        assert server["tools"][tool]["approval_mode"] == "prompt"


def test_all_shared_skills_have_codex_frontmatter_names():
    skill_dirs = sorted(path for path in (ROOT / "skills").iterdir() if path.is_dir())
    assert {path.name for path in skill_dirs} == {
        "ecosystem-defrag",
        "env-inspect",
        "package-install",
        "sandbox-run",
        "win-setup",
        "workflow-plan",
    }
    for directory in skill_dirs:
        text = (directory / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n")
        frontmatter = text.split("---", 2)[1]
        assert f"\nname: {directory.name}\n" in f"\n{frontmatter}\n"
        assert "\ndescription:" in f"\n{frontmatter}\n"


def test_claude_commands_are_thin_namespaced_adapters_to_shared_skills():
    routes = {
        "env.md": "windows-dev-agent:env-inspect",
        "plan.md": "windows-dev-agent:workflow-plan",
        "defrag.md": "windows-dev-agent:ecosystem-defrag",
    }
    for filename, skill in routes.items():
        text = (ROOT / "commands" / filename).read_text(encoding="utf-8")
        assert skill in text
        assert "## Procedure" not in text


def test_codex_project_scoped_tools_require_explicit_project_identity():
    tools = {tool["name"]: tool for tool in codex_server.TOOLS}
    for name, argument in codex_server.PROJECT_ARG_BY_TOOL.items():
        schema = tools[name]["inputSchema"]
        assert argument in schema["properties"]
        assert argument in schema["required"]


def test_codex_rejects_project_scoped_call_without_current_project():
    response = run(
        codex_server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "workflow_plan", "arguments": {"task": "run tests"}},
            }
        )
    )
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["status"] == "invalid_input"
    assert "project directory" in payload["error"]


def test_codex_runtime_binding_does_not_leak_into_shared_claude_server(monkeypatch, tmp_path: Path):
    previous = (common.DATA_DIR, common.LOG_FILE, common.ENVIRONMENT_CACHE_FILE)
    monkeypatch.delenv("WINDOWS_DEV_AGENT_DATA_DIR", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    project = tmp_path / "project"
    project.mkdir()
    expected = resolve_codex_data_dir()

    with codex_server._runtime_binding(project):
        assert common.DATA_DIR == expected
        assert common.LOG_FILE == expected / "agent.log"
        assert os.environ["WINDOWS_DEV_AGENT_PROJECT_DIR"] == str(project)

    assert (common.DATA_DIR, common.LOG_FILE, common.ENVIRONMENT_CACHE_FILE) == previous


def test_codex_initialize_explains_project_and_permission_boundaries():
    response = run(
        codex_server.handle_request(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
    )
    instructions = response["result"]["instructions"]
    assert "project directory" in instructions
    assert "approval" in instructions
    assert response["result"]["serverInfo"]["version"] == __version__


def test_codex_ecosystem_response_adds_host_inventory(tmp_path: Path):
    (tmp_path / ".agents").mkdir()
    response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"status": "ok", "inventory": {"agent_configs": []}}
                    ),
                }
            ]
        },
    }
    augmented = codex_server._augment_ecosystem_response(response, tmp_path)
    payload = json.loads(augmented["result"]["content"][0]["text"])
    inventory = payload["inventory"]
    assert "codex_plugins" in inventory
    assert str(tmp_path / ".agents") in inventory["agent_configs"]


def test_codex_gate_denies_forbidden_but_never_emulates_ask(monkeypatch):
    monkeypatch.setattr(codex_gate, "append_event", lambda *_args, **_kwargs: None)
    assert codex_gate.evaluate_hook_event(
        {"tool_name": "Bash", "tool_input": {"command": "git status --short"}}
    ) is None
    assert codex_gate.evaluate_hook_event(
        {
            "tool_name": CODEX_PREFIX + "package_install",
            "tool_input": {"package_id": "Python.Python.3.12", "execute": True},
        }
    ) is None
    forbidden = codex_gate.evaluate_hook_event(
        {"tool_name": "Bash", "tool_input": {"command": "format C:"}}
    )
    assert forbidden["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_codex_permission_request_allows_plan_only_and_defers_execution(monkeypatch):
    monkeypatch.setattr(codex_permission, "append_event", lambda *_args, **_kwargs: None)
    tool_name = CODEX_PREFIX + "package_install"
    planned = codex_permission.evaluate_permission_request(
        {
            "tool_name": tool_name,
            "tool_input": {"package_id": "Python.Python.3.12", "execute": False},
        }
    )
    assert planned["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"
    assert planned["hookSpecificOutput"]["decision"]["behavior"] == "allow"
    assert codex_permission.evaluate_permission_request(
        {
            "tool_name": tool_name,
            "tool_input": {"package_id": "Python.Python.3.12", "execute": True},
        }
    ) is None


def test_codex_hook_config_uses_codex_contract_and_not_plugin_data_cross_process_assumption():
    raw = (ROOT / "hooks" / "codex-hooks.json").read_text(encoding="utf-8")
    config = json.loads(raw)
    assert "permissionDecision: ask" not in raw
    pretool_matcher = config["hooks"]["PreToolUse"][0]["matcher"]
    permission_matcher = config["hooks"]["PermissionRequest"][0]["matcher"]
    assert "Bash" in pretool_matcher
    assert "windows_dev_agent" in pretool_matcher
    assert "PowerShell" not in pretool_matcher
    assert "windows_dev_agent" in permission_matcher
    commands = [
        hook["command"]
        for entries in config["hooks"].values()
        for entry in entries
        for hook in entry.get("hooks", [])
    ]
    assert commands
    assert all("${PLUGIN_ROOT}" in command for command in commands)
    assert all("${PLUGIN_DATA}" not in command for command in commands)


def test_codex_trace_does_not_infer_success_or_persist_payload():
    event = codex_trace.event_from_hook(
        {
            "session_id": "s1",
            "tool_name": CODEX_PREFIX + "package_search",
            "tool_input": {"query": "secret"},
            "tool_response": {"stdout": "secret"},
        }
    )
    assert event["success"] is None
    assert event["host"] == "codex"
    assert "tool_input" not in event
    assert "tool_response" not in event
    assert "secret" not in json.dumps(event)


def test_codex_stop_hook_emits_valid_json_from_deterministic_codex_data_root(tmp_path: Path):
    codex_home = tmp_path / "codex-home"
    data_dir = codex_home / "plugins" / "data" / "windows-dev-agent"
    data_dir.mkdir(parents=True)
    (data_dir / "agent.log").write_text(
        json.dumps(
            {
                "event": "PostToolUse",
                "success": None,
                "session_id": "session-codex",
                "tool_name": CODEX_PREFIX + "env_inspect",
                "host": "codex",
            }
        )
        + "\n",
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
    assert "systemMessage" in output
    assert "execution_failures=0" in output["systemMessage"]


def test_codex_data_dir_uses_codex_home_not_hook_only_plugin_data(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("WINDOWS_DEV_AGENT_DATA_DIR", raising=False)
    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path / "hook-only"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    assert resolve_codex_data_dir() == tmp_path / "codex-home" / "plugins" / "data" / "windows-dev-agent"
