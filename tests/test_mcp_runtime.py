"""Runtime-level tests for the MCP server without launching an MCP client."""

import asyncio
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


def test_package_install_defaults_to_plan_only():
    result = run(server.handle_package_install({"package_id": "Python.Python.3.12"}))
    assert result["status"] == "planned"
    assert result["requires_user_approval"] is True
    assert result["argv"][0] == "winget"


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
