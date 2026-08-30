"""Runtime-level tests for the MCP server without launching an MCP client."""

import asyncio
import json
from pathlib import Path

from src.mcp import server


def run(coro):
    return asyncio.run(coro)


def test_tools_list_exposes_real_surface():
    response = run(server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == [
        "env_inspect",
        "tool_discover",
        "capability_run",
        "workflow_plan",
        "package_install",
        "sandbox_run",
        "ecosystem_scan",
        "logs_query",
        "mcp_audit",
    ]


def test_workflow_plan_is_not_placeholder():
    result = run(server.handle_workflow_plan({"task": "run the Python tests"}))
    assert result["status"] == "planned"
    assert len(result["phases"]) >= 4
    assert result["candidate_capabilities"]
    assert any(candidate["capability"] == "test-python" for candidate in result["candidate_capabilities"])


def test_capability_run_defaults_to_claude_project_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WINDOWS_DEV_AGENT_PROJECT_DIR", str(tmp_path))
    observed = {}

    def fake_run_capability(capability, **kwargs):
        observed["capability"] = capability
        observed.update(kwargs)
        return {"status": "planned"}

    monkeypatch.setattr(server, "run_capability", fake_run_capability)
    result = run(server.handle_capability_run({"capability": "test-python"}))
    assert result["status"] == "planned"
    assert observed["capability"] == "test-python"
    assert Path(observed["cwd"]) == tmp_path.resolve()


def test_package_install_defaults_to_plan_only():
    result = run(server.handle_package_install({"package_id": "Python.Python.3.12"}))
    assert result["status"] == "planned"
    assert result["requires_user_approval"] is True
    assert result["argv"][0] == "winget"


def test_package_install_rejects_unknown_source():
    result = run(
        server.handle_package_install(
            {"package_id": "Python.Python.3.12", "source": "definitely-not-a-manager"}
        )
    )
    assert result["status"] == "invalid_input"


def test_package_install_cannot_execute_without_acknowledgement(monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda _name: "fake.exe")
    called = False

    def fake_run(*_args, **_kwargs):
        nonlocal called
        called = True
        return {"succeeded": True}

    monkeypatch.setattr(server, "_run", fake_run)
    result = run(
        server.handle_package_install(
            {"package_id": "Python.Python.3.12", "execute": True, "user_approved": False}
        )
    )
    assert result["status"] == "approval_required"
    assert called is False


def test_windows_sandbox_plan_does_not_materialize_bundle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WINDOWS_DEV_AGENT_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_choose_sandbox", lambda _environment: "windows_sandbox")
    monkeypatch.setattr(server, "_windows_sandbox_executable", lambda: "WindowsSandbox.exe")

    def must_not_run(_command):
        raise AssertionError("plan-only sandbox call materialized a bundle")

    monkeypatch.setattr(server, "_prepare_windows_sandbox", must_not_run)
    result = run(
        server.handle_sandbox_run(
            {"command": "echo safe-plan", "environment": "windows_sandbox", "execute": False}
        )
    )
    assert result["status"] == "planned"
    assert result["config_materialized_on_execute"] is True
    assert "config_path" not in result


def test_ecosystem_scan_is_read_only_and_project_scoped(tmp_path: Path, monkeypatch):
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers":{"example":{"command":"python","args":["server.py"]}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(server.shutil, "which", lambda _name: None)
    result = run(server.handle_ecosystem_scan({"cwd": str(tmp_path)}))
    assert result["status"] == "ok"
    assert result["inventory"]["mcp"][0]["servers"][0]["name"] == "example"
    assert sorted(path.name for path in tmp_path.iterdir()) == [".mcp.json"]


def test_logs_query_reads_persistent_log_path(tmp_path: Path, monkeypatch):
    log_file = tmp_path / "agent.log"
    log_file.write_text(
        json.dumps(
            {
                "event": "PreToolUse",
                "success": True,
                "tool_name": "mcp__windows-dev-agent__package_install",
                "permission_decision": "ask",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "LOG_FILE", log_file)
    result = run(server.handle_logs_query({"filter": "approvals"}))
    assert result["matched"] == 1
    assert result["events"][0]["permission_decision"] == "ask"


def test_mcp_audit_redacts_env_values(tmp_path: Path):
    config = tmp_path / "mcp.json"
    config.write_text(
        '{"mcpServers":{"example":{"command":"python","args":["server.py"],"env":{"TOKEN":"secret"}}}}',
        encoding="utf-8",
    )
    result = run(server.handle_mcp_audit({"config_path": str(config)}))
    server_entry = next(
        srv
        for cfg in result["configs"]
        if cfg["file"] == str(config.resolve())
        for srv in cfg["servers"]
    )
    assert server_entry["has_env"] is True
    assert "secret" not in str(result)
