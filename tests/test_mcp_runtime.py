"""Runtime-level contract tests for the shared MCP server."""

import asyncio
import json
from pathlib import Path

from src.mcp import server


def run(coro):
    return asyncio.run(coro)


def _tool(name: str):
    return next(tool for tool in server.TOOLS if tool["name"] == name)


def test_tools_list_exposes_exact_runtime_surface_and_no_fake_approval_field():
    response = run(server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == [
        "env_inspect", "tool_discover", "capability_run", "workflow_plan", "package_search",
        "package_install", "sandbox_run", "ecosystem_scan", "logs_query", "mcp_audit",
    ]
    for name in ("capability_run", "package_install", "sandbox_run"):
        assert "user_approved" not in _tool(name)["inputSchema"]["properties"]


def test_subprocesses_cannot_consume_mcp_stdin(monkeypatch):
    observed = {}

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return Result()

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    assert server._run(["probe"])["succeeded"] is True
    assert observed["stdin"] is server.subprocess.DEVNULL


def test_workflow_plan_is_project_bound_and_not_placeholder(tmp_path: Path):
    result = run(server.handle_workflow_plan({"task": "run the Python tests", "cwd": str(tmp_path)}))
    assert result["status"] == "planned"
    assert result["project_dir"] == str(tmp_path.resolve())
    assert len(result["phases"]) == 4
    assert any(candidate["capability"] == "test-python" for candidate in result["candidate_capabilities"])


def test_capability_run_uses_explicit_project_dir(tmp_path: Path, monkeypatch):
    observed = {}

    def fake_run_capability(capability, **kwargs):
        observed["capability"] = capability
        observed.update(kwargs)
        return {"status": "planned"}

    monkeypatch.setattr(server, "run_capability", fake_run_capability)
    result = run(server.handle_capability_run({"capability": "test-python", "cwd": str(tmp_path)}))
    assert result["status"] == "planned"
    assert Path(observed["cwd"]) == tmp_path.resolve()
    assert "user_approved" not in observed


def test_package_search_is_read_only_and_does_not_accept_source_agreements(monkeypatch):
    observed = {}
    monkeypatch.setattr(server.shutil, "which", lambda name: f"C:\\bin\\{name}.exe" if name == "winget" else None)

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        return {"succeeded": True, "returncode": 0, "stdout": "Python.Python.3.12", "stderr": "", "argv": argv}

    monkeypatch.setattr(server, "_run", fake_run)
    result = run(server.handle_package_search({"query": "Python 3.12", "source": "winget"}))
    assert result["status"] == "completed"
    assert "install" not in observed["argv"]
    assert "--accept-source-agreements" not in observed["argv"]


def test_package_install_plan_and_execute_share_one_host_authority_boundary(tmp_path: Path, monkeypatch):
    plan = run(server.handle_package_install({"package_id": "Python.Python.3.12"}))
    assert plan["status"] == "planned"
    assert plan["requires_host_approval"] is True

    cache = tmp_path / "environment.json"
    cache.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(server, "ENVIRONMENT_CACHE_FILE", cache)
    monkeypatch.setattr(server.shutil, "which", lambda _name: "fake.exe")
    monkeypatch.setattr(
        server,
        "_run",
        lambda argv, **_kwargs: {"succeeded": True, "returncode": 0, "stdout": "installed", "stderr": "", "argv": argv},
    )
    executed = run(server.handle_package_install({"package_id": "Python.Python.3.12", "execute": True}))
    assert executed["status"] == "completed"
    assert executed["execution_started"] is True
    assert executed["environment_cache_invalidated"] is True
    assert not cache.exists()


def test_failed_executed_install_also_invalidates_cache(tmp_path: Path, monkeypatch):
    cache = tmp_path / "environment.json"
    cache.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(server, "ENVIRONMENT_CACHE_FILE", cache)
    monkeypatch.setattr(server.shutil, "which", lambda _name: "fake.exe")
    monkeypatch.setattr(
        server,
        "_run",
        lambda argv, **_kwargs: {"succeeded": False, "returncode": 1, "stdout": "partial", "stderr": "failed", "argv": argv},
    )
    result = run(server.handle_package_install({"package_id": "Python.Python.3.12", "execute": True}))
    assert result["status"] == "failed"
    assert result["execution_started"] is True
    assert not cache.exists()


def test_sandbox_auto_requires_isolation_discriminator(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda _name: "available.exe")
    monkeypatch.setattr(server, "_windows_sandbox_executable", lambda: "WindowsSandbox.exe")
    result = run(server.handle_sandbox_run({"command": "echo hello", "workspace_folder": str(tmp_path), "environment": "auto"}))
    assert result["status"] == "invalid_input"


def test_untrusted_windows_auto_requires_reachable_payload(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server, "_windows_sandbox_executable", lambda: "WindowsSandbox.exe")
    missing = run(
        server.handle_sandbox_run(
            {
                "command": ".\\untrusted.exe",
                "workspace_folder": str(tmp_path),
                "environment": "auto",
                "isolation_requirement": "untrusted_windows",
            }
        )
    )
    assert missing["status"] == "invalid_input"
    assert "payload_paths" in missing["error"]

    payload = tmp_path / "untrusted.exe"
    payload.write_bytes(b"test")
    planned = run(
        server.handle_sandbox_run(
            {
                "command": ".\\untrusted.exe",
                "workspace_folder": str(tmp_path),
                "environment": "auto",
                "isolation_requirement": "untrusted_windows",
                "payload_paths": ["untrusted.exe"],
            }
        )
    )
    assert planned["status"] == "planned"
    assert planned["environment"] == "windows_sandbox"
    assert planned["payload_paths"] == ["untrusted.exe"]


def test_payload_rejects_workspace_escape_and_symlink(tmp_path: Path):
    outside = tmp_path.parent / "outside.bin"
    outside.write_bytes(b"x")
    sources, error = server._payload_sources(tmp_path, ["../outside.bin"])
    assert sources is None and "workspace" in error


def test_windows_sandbox_stages_payload_read_only_bundle(tmp_path: Path, monkeypatch):
    payload = tmp_path / "artifact.bin"
    payload.write_bytes(b"payload")
    monkeypatch.setattr(server, "_windows_sandbox_executable", lambda: "WindowsSandbox.exe")
    sources, error = server._payload_sources(tmp_path, ["artifact.bin"])
    assert error is None
    config, argv = server._prepare_windows_sandbox("type artifact.bin", sources)
    try:
        staged = config.parent / "payload" / "artifact.bin"
        assert staged.read_bytes() == b"payload"
        wsb = config.read_text(encoding="utf-8")
        assert "<ReadOnly>true</ReadOnly>" in wsb
        assert "<Networking>Disable</Networking>" in wsb
        assert "<ClipboardRedirection>Disable</ClipboardRedirection>" in wsb
        assert "C:\\WDAShare\\payload" in (config.parent / "run.cmd").read_text(encoding="utf-8")
        assert argv[0] == "WindowsSandbox.exe"
    finally:
        import shutil
        shutil.rmtree(config.parent, ignore_errors=True)


def test_wsl_route_enters_project_and_uses_portable_sh(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda name: "wsl.exe" if name in {"wsl", "wsl.exe"} else None)
    result = run(
        server.handle_sandbox_run(
            {
                "command": "pwd",
                "workspace_folder": str(tmp_path),
                "environment": "wsl",
                "execute": False,
            }
        )
    )
    assert result["argv"][:4] == ["wsl.exe", "--cd", str(tmp_path.resolve()), "--"]
    assert result["argv"][4:6] == ["sh", "-lc"]


def test_ecosystem_scan_project_scope_does_not_require_host_inventory(tmp_path: Path, monkeypatch):
    (tmp_path / ".mcp.json").write_text('{"mcpServers":{"example":{"command":"python"}}}', encoding="utf-8")
    monkeypatch.setattr(server.shutil, "which", lambda _name: (_ for _ in ()).throw(AssertionError("host executable lookup should not run")))
    result = run(server.handle_ecosystem_scan({"cwd": str(tmp_path), "include_host": False}))
    assert result["status"] == "ok"
    assert result["inventory"]["host_inventory_included"] is False
    assert result["inventory"]["mcp"][0]["servers"][0]["name"] == "example"


def test_package_inventory_requires_explicit_host_scope(tmp_path: Path):
    result = run(server.handle_ecosystem_scan({"cwd": str(tmp_path), "include_packages": True}))
    assert result["status"] == "invalid_input"


def test_logs_query_filters_host_neutral_permission_decisions(tmp_path: Path, monkeypatch):
    log = tmp_path / "agent.log"
    log.write_text(
        "\n".join(
            [
                json.dumps({"permission_decision": "ask", "tool_name": "one"}),
                json.dumps({"permission_decision": "allow", "tool_name": "two"}),
                json.dumps({"permission_decision": "host-default", "tool_name": "three"}),
            ]
        ) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "LOG_FILE", log)
    result = run(server.handle_logs_query({"filter": "approvals"}))
    assert result["matched"] == 2


def test_mcp_audit_defaults_to_project_and_redacts_env_values(tmp_path: Path):
    config = tmp_path / ".mcp.json"
    config.write_text(
        '{"mcpServers":{"example":{"command":"python","args":["server.py"],"env":{"TOKEN":"secret"}}}}',
        encoding="utf-8",
    )
    result = run(server.handle_mcp_audit({"cwd": str(tmp_path)}))
    assert result["host_inventory_included"] is False
    assert result["server_count"] == 1
    assert "secret" not in str(result)
