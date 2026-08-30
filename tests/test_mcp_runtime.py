"""Runtime-level contract tests for the MCP server."""

import asyncio
import json
from pathlib import Path

from src.mcp import server


def run(coro):
    return asyncio.run(coro)


def test_tools_list_exposes_exact_runtime_surface():
    response = run(server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == [
        "env_inspect",
        "tool_discover",
        "capability_run",
        "workflow_plan",
        "package_search",
        "package_install",
        "sandbox_run",
        "ecosystem_scan",
        "logs_query",
        "mcp_audit",
    ]


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
    result = server._run(["probe"])
    assert result["succeeded"] is True
    assert observed["stdin"] is server.subprocess.DEVNULL


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


def test_package_search_produces_read_only_candidate_evidence(monkeypatch):
    observed = {}
    monkeypatch.setattr(server.shutil, "which", lambda name: f"C:\\bin\\{name}.exe" if name == "winget" else None)

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return {"succeeded": True, "returncode": 0, "stdout": "Python.Python.3.12", "stderr": "", "argv": argv}

    monkeypatch.setattr(server, "_run", fake_run)
    result = run(server.handle_package_search({"query": "Python 3.12", "source": "winget"}))
    assert result["status"] == "completed"
    assert observed["argv"] == [
        "winget",
        "search",
        "--query",
        "Python 3.12",
        "--source",
        "winget",
        "--disable-interactivity",
    ]
    assert "install" not in observed["argv"]
    assert "--accept-source-agreements" not in observed["argv"]


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


def test_successful_package_install_invalidates_environment_cache(tmp_path: Path, monkeypatch):
    cache = tmp_path / "environment.json"
    cache.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(server, "ENVIRONMENT_CACHE_FILE", cache)
    monkeypatch.setattr(server.shutil, "which", lambda _name: "fake.exe")
    monkeypatch.setattr(
        server,
        "_run",
        lambda argv, **_kwargs: {
            "succeeded": True,
            "returncode": 0,
            "stdout": "installed",
            "stderr": "",
            "argv": argv,
        },
    )

    result = run(
        server.handle_package_install(
            {"package_id": "Python.Python.3.12", "execute": True, "user_approved": True}
        )
    )
    assert result["status"] == "completed"
    assert result["environment_cache_invalidated"] is True
    assert not cache.exists()


def test_sandbox_auto_requires_isolation_discriminator(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WINDOWS_DEV_AGENT_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(server.shutil, "which", lambda _name: "available.exe")
    monkeypatch.setattr(server, "_windows_sandbox_executable", lambda: "WindowsSandbox.exe")
    result = run(server.handle_sandbox_run({"command": "echo hello", "environment": "auto"}))
    assert result["status"] == "invalid_input"
    assert "isolation_requirement" in result["error"]


def test_untrusted_windows_auto_route_does_not_choose_available_wsl(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WINDOWS_DEV_AGENT_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(server.shutil, "which", lambda _name: "wsl.exe")
    monkeypatch.setattr(server, "_windows_sandbox_executable", lambda: "WindowsSandbox.exe")
    result = run(
        server.handle_sandbox_run(
            {
                "command": "untrusted.exe",
                "environment": "auto",
                "isolation_requirement": "untrusted_windows",
                "execute": False,
            }
        )
    )
    assert result["status"] == "planned"
    assert result["environment"] == "windows_sandbox"


def test_project_reproducibility_requires_project_devcontainer(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WINDOWS_DEV_AGENT_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(server.shutil, "which", lambda name: "devcontainer.exe" if name == "devcontainer" else None)

    missing = run(
        server.handle_sandbox_run(
            {
                "command": "pytest",
                "environment": "auto",
                "isolation_requirement": "project_reproducibility",
                "execute": False,
            }
        )
    )
    assert missing["status"] == "unavailable"

    (tmp_path / ".devcontainer").mkdir()
    planned = run(
        server.handle_sandbox_run(
            {
                "command": "pytest",
                "environment": "auto",
                "isolation_requirement": "project_reproducibility",
                "execute": False,
            }
        )
    )
    assert planned["status"] == "planned"
    assert planned["environment"] == "dev_container"


def test_windows_sandbox_plan_does_not_materialize_bundle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WINDOWS_DEV_AGENT_PROJECT_DIR", str(tmp_path))
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


def test_logs_query_labels_persistent_history_scope(tmp_path: Path, monkeypatch):
    log_file = tmp_path / "agent.log"
    log_file.write_text(
        json.dumps(
            {
                "event": "PreToolUse",
                "success": True,
                "tool_name": "mcp__plugin_windows-dev-agent_windows-dev-agent__package_install",
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
    assert result["scope"] == "persistent_history"


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
