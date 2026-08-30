"""Runtime-level contract tests for the shared MCP server."""

import asyncio
import json
from pathlib import Path

from src.discovery import discovery as discovery_module
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
        properties = _tool(name)["inputSchema"]["properties"]
        assert "user_approved" not in properties
        assert "plan_fingerprint" in properties


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
    assert result["execution_started"] is True
    assert observed["stdin"] is server.subprocess.DEVNULL


def test_subprocess_spawn_failure_is_not_reported_as_started(monkeypatch):
    def fail_before_start(*_args, **_kwargs):
        raise OSError("spawn failed")

    monkeypatch.setattr(server.subprocess, "run", fail_before_start)
    result = server._run(["probe"])
    assert result["succeeded"] is False
    assert result["execution_started"] is False
    assert "timed_out" not in result


def test_subprocess_timeout_preserves_that_execution_started(monkeypatch):
    def timeout_after_start(argv, **_kwargs):
        raise server.subprocess.TimeoutExpired(argv, 1)

    monkeypatch.setattr(server.subprocess, "run", timeout_after_start)
    result = server._run(["probe"], timeout=1)
    assert result["succeeded"] is False
    assert result["execution_started"] is True
    assert result["timed_out"] is True


def test_env_inspect_hard_failure_keeps_canonical_unknown_snapshot(monkeypatch):
    class BrokenDiscovery:
        def __init__(self, *_args, **_kwargs):
            raise OSError("cache root unavailable")

    monkeypatch.setattr(discovery_module, "EnvironmentDiscovery", BrokenDiscovery)
    result = run(server.handle_env_inspect({"force_refresh": True}))
    assert result["status"] == "degraded"
    assert set(result) == {"status", "snapshot"}
    snapshot = result["snapshot"]
    assert snapshot["success"] is False
    assert snapshot["runtimes"]["node"]["available"] is None
    assert snapshot["runtimes"]["rust"]["available"] is None
    assert snapshot["git"]["available"] is None
    assert "cache root unavailable" in snapshot["errors"][0]


def test_tool_discover_probes_exact_resolved_executable(tmp_path: Path, monkeypatch):
    git_exe = str((tmp_path / "git.exe").resolve())
    observed = []
    monkeypatch.setattr(server.shutil, "which", lambda name: git_exe if name == "git" else None)

    def fake_run(argv, **_kwargs):
        observed.append(argv)
        return {
            "succeeded": True,
            "returncode": 0,
            "stdout": "git version test",
            "stderr": "",
            "argv": argv,
            "execution_started": True,
        }

    monkeypatch.setattr(server, "_run", fake_run)
    result = run(server.handle_tool_discover({"category": "vcs"}))
    assert result["vcs"]["git"]["path"] == git_exe
    assert observed == [[git_exe, "--version"]]


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
    assert observed["plan_fingerprint"] is None
    assert "user_approved" not in observed


def test_package_search_is_read_only_and_binds_resolved_manager(tmp_path: Path, monkeypatch):
    observed = {}
    winget = str((tmp_path / "winget.exe").resolve())
    monkeypatch.setattr(server.shutil, "which", lambda name: winget if name == "winget" else None)

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        return {
            "succeeded": True,
            "returncode": 0,
            "stdout": "Python.Python.3.12",
            "stderr": "",
            "argv": argv,
            "execution_started": True,
        }

    monkeypatch.setattr(server, "_run", fake_run)
    result = run(server.handle_package_search({"query": "Python 3.12", "source": "winget"}))
    assert result["status"] == "completed"
    assert result["resolved_executable"] == winget
    assert observed["argv"][0] == winget
    assert "install" not in observed["argv"]
    assert "--accept-source-agreements" not in observed["argv"]


def test_package_install_plan_and_execute_share_one_host_authority_boundary(tmp_path: Path, monkeypatch):
    winget = str((tmp_path / "winget.exe").resolve())
    monkeypatch.setattr(server.shutil, "which", lambda _name: winget)
    plan = run(server.handle_package_install({"package_id": "Python.Python.3.12"}))
    assert plan["status"] == "planned"
    assert plan["requires_host_approval"] is True
    assert plan["resolved_executable"] == winget
    assert plan["argv"][0] == winget
    assert len(plan["plan_fingerprint"]) == 64

    cache = tmp_path / "environment.json"
    cache.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(server, "ENVIRONMENT_CACHE_FILE", cache)
    monkeypatch.setattr(
        server,
        "_run",
        lambda argv, **_kwargs: {
            "succeeded": True,
            "returncode": 0,
            "stdout": "installed",
            "stderr": "",
            "argv": argv,
            "execution_started": True,
        },
    )
    executed = run(
        server.handle_package_install(
            {
                "package_id": "Python.Python.3.12",
                "execute": True,
                "plan_fingerprint": plan["plan_fingerprint"],
            }
        )
    )
    assert executed["status"] == "completed"
    assert executed["execution_started"] is True
    assert executed["environment_cache_invalidated"] is True
    assert not cache.exists()


def test_package_install_missing_plan_fingerprint_never_executes(tmp_path: Path, monkeypatch):
    winget = str((tmp_path / "winget.exe").resolve())
    monkeypatch.setattr(server.shutil, "which", lambda _name: winget)
    monkeypatch.setattr(server, "_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")))
    result = run(server.handle_package_install({"package_id": "Python.Python.3.12", "execute": True}))
    assert result["status"] == "stale_plan"
    assert result["execution_started"] is False


def test_package_install_changed_executable_invalidates_reviewed_plan(tmp_path: Path, monkeypatch):
    first = str((tmp_path / "winget-a.exe").resolve())
    second = str((tmp_path / "winget-b.exe").resolve())
    resolutions = iter((first, second))
    monkeypatch.setattr(server.shutil, "which", lambda _name: next(resolutions))
    monkeypatch.setattr(server, "_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale plan must not execute")))
    plan = run(server.handle_package_install({"package_id": "Python.Python.3.12"}))
    result = run(
        server.handle_package_install(
            {
                "package_id": "Python.Python.3.12",
                "execute": True,
                "plan_fingerprint": plan["plan_fingerprint"],
            }
        )
    )
    assert result["status"] == "stale_plan"
    assert result["execution_started"] is False
    assert result["resolved_executable"] == second
    assert result["submitted_plan_fingerprint"] == plan["plan_fingerprint"]


def test_failed_executed_install_also_invalidates_cache(tmp_path: Path, monkeypatch):
    winget = str((tmp_path / "winget.exe").resolve())
    monkeypatch.setattr(server.shutil, "which", lambda _name: winget)
    plan = run(server.handle_package_install({"package_id": "Python.Python.3.12"}))
    cache = tmp_path / "environment.json"
    cache.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(server, "ENVIRONMENT_CACHE_FILE", cache)
    monkeypatch.setattr(
        server,
        "_run",
        lambda argv, **_kwargs: {
            "succeeded": False,
            "returncode": 1,
            "stdout": "partial",
            "stderr": "failed",
            "argv": argv,
            "execution_started": True,
        },
    )
    result = run(
        server.handle_package_install(
            {
                "package_id": "Python.Python.3.12",
                "execute": True,
                "plan_fingerprint": plan["plan_fingerprint"],
            }
        )
    )
    assert result["status"] == "failed"
    assert result["execution_started"] is True
    assert result["environment_cache_invalidated"] is True
    assert not cache.exists()


def test_package_install_spawn_failure_preserves_environment_cache(tmp_path: Path, monkeypatch):
    winget = str((tmp_path / "winget.exe").resolve())
    monkeypatch.setattr(server.shutil, "which", lambda _name: winget)
    plan = run(server.handle_package_install({"package_id": "Python.Python.3.12"}))
    cache = tmp_path / "environment.json"
    cache.write_text("still-valid", encoding="utf-8")
    monkeypatch.setattr(server, "ENVIRONMENT_CACHE_FILE", cache)
    monkeypatch.setattr(
        server,
        "_run",
        lambda argv, **_kwargs: {
            "succeeded": False,
            "error": "spawn failed",
            "argv": argv,
            "execution_started": False,
        },
    )
    result = run(
        server.handle_package_install(
            {
                "package_id": "Python.Python.3.12",
                "execute": True,
                "plan_fingerprint": plan["plan_fingerprint"],
            }
        )
    )
    assert result["status"] == "failed"
    assert result["execution_started"] is False
    assert result["environment_cache_invalidated"] is False
    assert cache.read_text(encoding="utf-8") == "still-valid"


def test_sandbox_auto_requires_isolation_discriminator(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda _name: "available.exe")
    monkeypatch.setattr(server, "_windows_sandbox_executable", lambda: "WindowsSandbox.exe")
    result = run(server.handle_sandbox_run({"command": "echo hello", "workspace_folder": str(tmp_path), "environment": "auto"}))
    assert result["status"] == "invalid_input"


def test_untrusted_windows_auto_requires_reachable_payload(tmp_path: Path, monkeypatch):
    sandbox_exe = str((tmp_path / "WindowsSandbox.exe").resolve())
    monkeypatch.setattr(server, "_windows_sandbox_executable", lambda: sandbox_exe)
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
    assert planned["resolved_executable"] == sandbox_exe
    assert len(planned["plan_fingerprint"]) == 64


def test_payload_rejects_workspace_escape_and_symlink(tmp_path: Path):
    outside = tmp_path.parent / "outside.bin"
    outside.write_bytes(b"x")
    sources, error = server._payload_sources(tmp_path, ["../outside.bin"])
    assert sources is None and "workspace" in error


def test_payload_rejects_overlapping_roots(tmp_path: Path):
    folder = tmp_path / "bundle"
    folder.mkdir()
    (folder / "inside.bin").write_bytes(b"x")
    sources, error = server._payload_sources(tmp_path, ["bundle", "bundle/inside.bin"])
    assert sources is None
    assert "overlap" in error


def test_payload_byte_budget_is_enforced(tmp_path: Path, monkeypatch):
    payload = tmp_path / "large.bin"
    payload.write_bytes(b"12345")
    monkeypatch.setattr(server, "MAX_SANDBOX_PAYLOAD_BYTES", 4)
    sources, error = server._payload_sources(tmp_path, ["large.bin"])
    assert sources is None
    assert "byte budget" in error


def test_windows_sandbox_stages_payload_read_only_bundle(tmp_path: Path):
    payload = tmp_path / "artifact.bin"
    payload.write_bytes(b"payload")
    sandbox_exe = str((tmp_path / "WindowsSandbox.exe").resolve())
    sources, error = server._payload_sources(tmp_path, ["artifact.bin"])
    assert error is None
    config, argv = server._prepare_windows_sandbox("type artifact.bin", sources, sandbox_exe)
    try:
        staged = config.parent / "payload" / "artifact.bin"
        assert staged.read_bytes() == b"payload"
        wsb = config.read_text(encoding="utf-8")
        assert "<ReadOnly>true</ReadOnly>" in wsb
        assert "<Networking>Disable</Networking>" in wsb
        assert "<ClipboardRedirection>Disable</ClipboardRedirection>" in wsb
        assert "C:\\WDAShare\\payload" in (config.parent / "run.cmd").read_text(encoding="utf-8")
        assert argv[0] == sandbox_exe
    finally:
        import shutil
        shutil.rmtree(config.parent, ignore_errors=True)


def test_failed_sandbox_staging_removes_partial_bundle(tmp_path: Path, monkeypatch):
    payload = tmp_path / "artifact.bin"
    payload.write_bytes(b"payload")
    staging = tmp_path / "staging"
    sandbox_exe = str((tmp_path / "WindowsSandbox.exe").resolve())
    monkeypatch.setattr(server.tempfile, "mkdtemp", lambda **_kwargs: str(staging))
    monkeypatch.setattr(server.shutil, "copy2", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy failed")))
    sources, error = server._payload_sources(tmp_path, ["artifact.bin"])
    assert error is None
    try:
        server._prepare_windows_sandbox("type artifact.bin", sources, sandbox_exe)
    except OSError:
        pass
    else:
        raise AssertionError("staging failure was not surfaced")
    assert not staging.exists()


def test_wsl_route_enters_project_and_uses_portable_sh(tmp_path: Path, monkeypatch):
    wsl_exe = str((tmp_path / "wsl.exe").resolve())
    monkeypatch.setattr(server.shutil, "which", lambda name: wsl_exe if name in {"wsl", "wsl.exe"} else None)
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
    assert result["argv"][:4] == [wsl_exe, "--cd", str(tmp_path.resolve()), "--"]
    assert result["argv"][4:6] == ["sh", "-lc"]


def test_captured_sandbox_spawn_failure_is_not_reported_as_started(tmp_path: Path, monkeypatch):
    wsl_exe = str((tmp_path / "wsl.exe").resolve())
    monkeypatch.setattr(server.shutil, "which", lambda name: wsl_exe if name in {"wsl", "wsl.exe"} else None)
    plan = run(
        server.handle_sandbox_run(
            {
                "command": "pwd",
                "workspace_folder": str(tmp_path),
                "environment": "wsl",
                "execute": False,
            }
        )
    )
    monkeypatch.setattr(
        server,
        "_run",
        lambda argv, **_kwargs: {
            "succeeded": False,
            "error": "spawn failed",
            "argv": argv,
            "execution_started": False,
        },
    )
    result = run(
        server.handle_sandbox_run(
            {
                "command": "pwd",
                "workspace_folder": str(tmp_path),
                "environment": "wsl",
                "execute": True,
                "plan_fingerprint": plan["plan_fingerprint"],
            }
        )
    )
    assert result["status"] == "failed"
    assert result["execution_started"] is False


def test_sandbox_changed_backend_executable_invalidates_plan(tmp_path: Path, monkeypatch):
    first = str((tmp_path / "wsl-a.exe").resolve())
    second = str((tmp_path / "wsl-b.exe").resolve())
    calls = iter((first, second))

    def resolve(name):
        if name == "wsl":
            return next(calls)
        return None

    monkeypatch.setattr(server.shutil, "which", resolve)
    monkeypatch.setattr(server, "_run", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale plan must not execute")))
    plan = run(server.handle_sandbox_run({"command": "pwd", "workspace_folder": str(tmp_path), "environment": "wsl"}))
    result = run(
        server.handle_sandbox_run(
            {
                "command": "pwd",
                "workspace_folder": str(tmp_path),
                "environment": "wsl",
                "execute": True,
                "plan_fingerprint": plan["plan_fingerprint"],
            }
        )
    )
    assert result["status"] == "stale_plan"
    assert result["execution_started"] is False
    assert result["resolved_executable"] == second


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


def test_mcp_audit_defaults_to_project_and_exposes_only_structural_metadata(tmp_path: Path):
    config = tmp_path / ".mcp.json"
    config.write_text(
        '{"mcpServers":{"example":{"command":"secret-cli --token secret","args":["server.py"],"env":{"TOKEN":"secret"}}}}',
        encoding="utf-8",
    )
    result = run(server.handle_mcp_audit({"cwd": str(tmp_path)}))
    assert result["host_inventory_included"] is False
    assert result["server_count"] == 1
    serialized = json.dumps(result)
    assert "secret" not in serialized
    entry = result["configs"][0]["servers"][0]
    assert entry["transport"] == "command"
    assert entry["has_command"] is True
    assert "command" not in entry


def test_json_config_read_budget_is_enforced(tmp_path: Path, monkeypatch):
    config = tmp_path / ".mcp.json"
    config.write_text('{"mcpServers":{}}', encoding="utf-8")
    monkeypatch.setattr(server, "MAX_JSON_CONFIG_BYTES", 4)
    data, error = server._safe_json(config)
    assert data is None
    assert "read limit" in error
