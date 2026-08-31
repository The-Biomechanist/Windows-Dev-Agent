"""Runtime-level contract tests for the shared MCP core."""

import asyncio
import json
from pathlib import Path
import shutil

from src.file_guard import ExecutableIdentity
from src.mcp import server
from src.windows_state import WslRouteState


def run(coro):
    return asyncio.run(coro)


def _tool(name: str):
    return next(tool for tool in server.TOOLS if tool["name"] == name)


def test_tools_list_exposes_exact_runtime_surface_and_strict_schemas():
    response = run(server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
    names = [tool["name"] for tool in response["result"]["tools"]]
    assert names == [
        "env_inspect", "tool_discover", "capability_run", "workflow_plan", "package_search",
        "package_install", "sandbox_run", "ecosystem_scan", "logs_query", "mcp_audit",
    ]
    for tool in response["result"]["tools"]:
        assert tool["inputSchema"]["additionalProperties"] is False
    for name in ("capability_run", "package_install", "sandbox_run"):
        properties = _tool(name)["inputSchema"]["properties"]
        assert "user_approved" not in properties
        assert "expected_executable" in properties
        assert "expected_executable_identity_kind" in properties
        assert "expected_executable_identity_sha256" in properties
    assert "isolation_requirement" in _tool("sandbox_run")["inputSchema"]["required"]
    assert not hasattr(server, "main_sync")


def test_tools_call_rejects_python_coercion_and_unknown_fields():
    bad_bool = run(
        server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "env_inspect", "arguments": {"force_refresh": "false"}},
            }
        )
    )
    assert bad_bool["error"]["code"] == -32602
    assert "boolean" in bad_bool["error"]["message"]

    unknown = run(
        server.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "env_inspect", "arguments": {"surprise": True}},
            }
        )
    )
    assert unknown["error"]["code"] == -32602
    assert "unknown tool argument" in unknown["error"]["message"]


def test_tool_argument_defaults_are_materialized_before_execution(monkeypatch):
    observed = {}

    async def fake_handler(args):
        observed.update(args)
        return {"status": "ok"}

    monkeypatch.setitem(server.HANDLERS, "env_inspect", fake_handler)
    response = run(
        server.handle_request(
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "env_inspect", "arguments": {}}}
        )
    )
    assert "result" in response
    assert observed == {"force_refresh": False}


def test_workflow_plan_is_project_bound_and_not_placeholder(tmp_path: Path):
    result = run(server.handle_workflow_plan({"task": "run the Python tests", "context": "", "cwd": str(tmp_path)}))
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
    result = run(
        server.handle_capability_run(
            {"capability": "test-python", "extra_args": [], "cwd": str(tmp_path), "execute": False, "timeout_seconds": 120}
        )
    )
    assert result["status"] == "planned"
    assert Path(observed["cwd"]) == tmp_path.resolve()
    assert observed["expected_executable"] is None
    assert observed["expected_executable_identity_kind"] is None
    assert observed["expected_executable_identity_sha256"] is None
    assert "user_approved" not in observed


def test_package_search_executes_the_exact_resolved_binary(monkeypatch):
    observed = {}
    monkeypatch.setattr(server, "resolve_executable", lambda name: "C:\\trusted\\winget.exe" if name == "winget" else None)

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

    monkeypatch.setattr(server, "run_bounded", fake_run)
    result = run(server.handle_package_search({"query": "Python 3.12", "source": "winget"}))
    assert result["status"] == "completed"
    assert observed["argv"][0] == "C:\\trusted\\winget.exe"
    assert "install" not in observed["argv"]
    assert "--accept-source-agreements" not in observed["argv"]


def test_package_install_invalidates_cache_generation_before_execution(tmp_path: Path, monkeypatch):
    observed = {}
    trusted = "C:\\trusted\\winget.exe"
    identity = ExecutableIdentity(kind="file", sha256="b" * 64)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)
    monkeypatch.setattr(server, "resolve_executable", lambda _name: trusted)
    monkeypatch.setattr(server, "executable_identity", lambda _path: identity)
    monkeypatch.setattr(server, "executable_identity_matches", lambda expected, actual: expected == actual)
    monkeypatch.setattr(server, "invalidate_environment_cache", lambda data_dir: observed.setdefault("invalidated", data_dir) is not None)

    def fake_run(argv, **_kwargs):
        assert "invalidated" in observed
        return {"succeeded": True, "returncode": 0, "stdout": "installed", "stderr": "", "argv": argv, "execution_started": True}

    monkeypatch.setattr(server, "run_bounded", fake_run)
    result = run(
        server.handle_package_install(
            {
                "package_id": "Python.Python.3.12",
                "source": "winget",
                "execute": True,
                "expected_executable": trusted,
                "expected_executable_identity_kind": identity.kind,
                "expected_executable_identity_sha256": identity.sha256,
            }
        )
    )
    assert result["status"] == "completed"
    assert result["environment_cache_invalidated"] is True
    assert observed["invalidated"] == tmp_path


def test_package_install_plan_never_executes_or_invalidates(monkeypatch):
    trusted = "C:\\trusted\\winget.exe"
    identity = ExecutableIdentity(kind="file", sha256="c" * 64)
    monkeypatch.setattr(server, "resolve_executable", lambda _name: trusted)
    monkeypatch.setattr(server, "executable_identity", lambda _path: identity)
    monkeypatch.setattr(server, "invalidate_environment_cache", lambda _data_dir: (_ for _ in ()).throw(AssertionError("plan must not invalidate")))
    monkeypatch.setattr(server, "run_bounded", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("plan must not execute")))
    result = run(server.handle_package_install({"package_id": "Python.Python.3.12", "source": "winget", "execute": False}))
    assert result["status"] == "planned"
    assert result["requires_host_approval"] is True
    assert result["executable"] == trusted
    assert result["executable_identity_kind"] == identity.kind
    assert result["executable_identity_sha256"] == identity.sha256
    assert result["argv"][0] == trusted


def test_plan_first_execution_refuses_missing_or_changed_executable_identity(tmp_path: Path, monkeypatch):
    trusted = "C:\\Windows\\System32\\wsl.exe"
    monkeypatch.setattr(server, "query_wsl_route_state", lambda _exe: WslRouteState(True, "Ubuntu"))
    identity = ExecutableIdentity(kind="file", sha256="d" * 64)
    monkeypatch.setattr(server, "_wsl_executable", lambda: trusted)
    monkeypatch.setattr(server, "executable_identity", lambda _path: identity)
    monkeypatch.setattr(server, "executable_identity_matches", lambda expected, actual: expected == actual)
    monkeypatch.setattr(server, "run_bounded", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale plan must not execute")))

    base = {
        "command": "pwd",
        "workspace_folder": str(tmp_path),
        "environment": "wsl",
        "isolation_requirement": "linux_compatibility",
        "payload_paths": [],
        "execute": True,
    }
    missing = run(server.handle_sandbox_run(base))
    assert missing["status"] == "invalid_input"
    assert missing["execution_started"] is False

    missing_identity = run(server.handle_sandbox_run({**base, "expected_executable": trusted}))
    assert missing_identity["status"] == "invalid_input"

    stale = run(
        server.handle_sandbox_run(
            {
                **base,
                "expected_executable": "C:\\other\\wsl.exe",
                "expected_executable_identity_kind": identity.kind,
                "expected_executable_identity_sha256": identity.sha256,
            }
        )
    )
    assert stale["status"] == "stale_plan"
    assert stale["execution_started"] is False


def test_every_sandbox_request_requires_semantic_isolation_requirement(tmp_path: Path):
    result = run(server.handle_sandbox_run({"command": "echo hello", "workspace_folder": str(tmp_path), "environment": "wsl"}))
    assert result["status"] == "invalid_input"
    assert "isolation_requirement" in result["error"]


def test_explicit_sandbox_backend_must_satisfy_requirement(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server, "_wsl_executable", lambda: "C:\\Windows\\System32\\wsl.exe")
    result = run(
        server.handle_sandbox_run(
            {
                "command": "echo hello",
                "workspace_folder": str(tmp_path),
                "environment": "wsl",
                "isolation_requirement": "untrusted_windows",
            }
        )
    )
    assert result["status"] == "invalid_input"
    assert "required backend is windows_sandbox" in result["error"]


def test_untrusted_windows_always_requires_reachable_payload(tmp_path: Path, monkeypatch):
    trusted = "C:\\Windows\\System32\\WindowsSandbox.exe"
    identity = ExecutableIdentity(kind="file", sha256="e" * 64)
    monkeypatch.setattr(server, "_windows_sandbox_executable", lambda: trusted)
    monkeypatch.setattr(server, "executable_identity", lambda _path: identity)
    missing = run(
        server.handle_sandbox_run(
            {
                "command": ".\\untrusted.exe",
                "workspace_folder": str(tmp_path),
                "environment": "windows_sandbox",
                "isolation_requirement": "untrusted_windows",
                "payload_paths": [],
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
                "environment": "windows_sandbox",
                "isolation_requirement": "untrusted_windows",
                "payload_paths": ["untrusted.exe"],
                "execute": False,
            }
        )
    )
    assert planned["status"] == "planned"
    assert planned["environment"] == "windows_sandbox"
    assert planned["executable"] == trusted
    assert planned["executable_identity_kind"] == identity.kind
    assert planned["executable_identity_sha256"] == identity.sha256


def test_payload_rejects_workspace_escape_and_overlapping_roots(tmp_path: Path):
    sources, error = server._payload_sources(tmp_path, ["../outside.bin"])
    assert sources is None and "workspace" in error

    folder = tmp_path / "bundle"
    folder.mkdir()
    (folder / "inside.bin").write_bytes(b"x")
    sources, error = server._payload_sources(tmp_path, ["bundle", "bundle/inside.bin"])
    assert sources is None and "overlap" in error


def test_payload_byte_budget_is_enforced(tmp_path: Path, monkeypatch):
    payload = tmp_path / "large.bin"
    payload.write_bytes(b"12345")
    monkeypatch.setattr(server, "MAX_SANDBOX_PAYLOAD_BYTES", 4)
    sources, error = server._payload_sources(tmp_path, ["large.bin"])
    assert sources is None
    assert "byte budget" in error


def test_windows_sandbox_config_is_outside_share_and_disables_exposure(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = workspace / "artifact.bin"
    payload.write_bytes(b"payload")
    data_dir = tmp_path / "data"
    trusted = "C:\\Windows\\System32\\WindowsSandbox.exe"
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    sources, error = server._payload_sources(workspace, ["artifact.bin"])
    assert error is None
    bundle_root, config, argv = server._prepare_windows_sandbox("type artifact.bin", workspace, sources, trusted)
    try:
        share = bundle_root / "share"
        staged = share / "payload" / "artifact.bin"
        assert staged.read_bytes() == b"payload"
        assert config.parent == bundle_root
        assert config.parent != share
        wsb = config.read_text(encoding="utf-8")
        assert f"<HostFolder>{share}" in wsb
        assert str(config) not in wsb
        for setting in (
            "<vGPU>Disable</vGPU>",
            "<Networking>Disable</Networking>",
            "<AudioInput>Disable</AudioInput>",
            "<VideoInput>Disable</VideoInput>",
            "<PrinterRedirection>Disable</PrinterRedirection>",
            "<ClipboardRedirection>Disable</ClipboardRedirection>",
            "<ReadOnly>true</ReadOnly>",
        ):
            assert setting in wsb
        assert "C:\\WDAShare\\payload" in (share / "run.cmd").read_text(encoding="utf-8")
        assert argv[0] == trusted
    finally:
        shutil.rmtree(bundle_root, ignore_errors=True)


def test_failed_sandbox_staging_removes_partial_bundle(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = workspace / "artifact.bin"
    payload.write_bytes(b"payload")
    data_dir = tmp_path / "data"
    staging = data_dir / "sandbox-runs" / "run-fixed"
    trusted = "C:\\Windows\\System32\\WindowsSandbox.exe"
    monkeypatch.setattr(server, "DATA_DIR", data_dir)

    def fake_mkdtemp(**_kwargs):
        staging.mkdir(parents=True)
        return str(staging)

    monkeypatch.setattr(server.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(server, "guarded_open_read", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read failed")))
    sources, error = server._payload_sources(workspace, ["artifact.bin"])
    assert error is None
    try:
        server._prepare_windows_sandbox("type artifact.bin", workspace, sources, trusted)
    except RuntimeError:
        pass
    else:
        raise AssertionError("staging failure was not surfaced")
    assert not staging.exists()


def test_wsl_route_uses_windows_owned_identity_and_project_cd(tmp_path: Path, monkeypatch):
    trusted_wsl = "C:\\Windows\\System32\\wsl.exe"
    monkeypatch.setattr(server, "query_wsl_route_state", lambda _exe: WslRouteState(True, "Ubuntu"))
    identity = ExecutableIdentity(kind="file", sha256="f" * 64)
    monkeypatch.setattr(server, "_wsl_executable", lambda: trusted_wsl)
    monkeypatch.setattr(server, "executable_identity", lambda _path: identity)
    result = run(
        server.handle_sandbox_run(
            {
                "command": "pwd",
                "workspace_folder": str(tmp_path),
                "environment": "wsl",
                "isolation_requirement": "linux_compatibility",
                "payload_paths": [],
                "execute": False,
            }
        )
    )
    assert result["executable"] == trusted_wsl
    assert result["executable_identity_kind"] == identity.kind
    assert result["executable_identity_sha256"] == identity.sha256
    assert result["argv"][:4] == [trusted_wsl, "--cd", str(tmp_path.resolve()), "--"]
    assert result["argv"][4:6] == ["sh", "-lc"]


def test_captured_sandbox_spawn_failure_is_not_reported_as_started(tmp_path: Path, monkeypatch):
    trusted_wsl = "C:\\Windows\\System32\\wsl.exe"
    monkeypatch.setattr(server, "query_wsl_route_state", lambda _exe: WslRouteState(True, "Ubuntu"))
    identity = ExecutableIdentity(kind="file", sha256="1" * 64)
    monkeypatch.setattr(server, "_wsl_executable", lambda: trusted_wsl)
    monkeypatch.setattr(server, "executable_identity", lambda _path: identity)
    monkeypatch.setattr(server, "executable_identity_matches", lambda expected, actual: expected == actual)
    monkeypatch.setattr(
        server,
        "run_bounded",
        lambda argv, **_kwargs: {"succeeded": False, "error": "spawn failed", "argv": argv, "execution_started": False},
    )
    result = run(
        server.handle_sandbox_run(
            {
                "command": "pwd",
                "workspace_folder": str(tmp_path),
                "environment": "wsl",
                "isolation_requirement": "linux_compatibility",
                "payload_paths": [],
                "execute": True,
                "expected_executable": trusted_wsl,
                "expected_executable_identity_kind": identity.kind,
                "expected_executable_identity_sha256": identity.sha256,
            }
        )
    )
    assert result["status"] == "failed"
    assert result["execution_started"] is False


def test_ecosystem_scan_project_scope_does_not_touch_host_executables(tmp_path: Path, monkeypatch):
    (tmp_path / ".mcp.json").write_text('{"mcpServers":{"example":{"command":"python"}}}', encoding="utf-8")
    monkeypatch.setattr(server, "resolve_executable", lambda _name: (_ for _ in ()).throw(AssertionError("host executable lookup should not run")))
    result = run(server.handle_ecosystem_scan({"cwd": str(tmp_path), "include_host": False, "include_packages": False}))
    assert result["status"] == "ok"
    assert result["inventory"]["host_inventory_included"] is False
    assert result["inventory"]["mcp"][0]["servers"][0]["name"] == "example"


def test_project_config_read_rejects_reparse_boundary(tmp_path: Path, monkeypatch):
    config = tmp_path / ".mcp.json"
    config.write_text('{"mcpServers":{"escaped":{"command":"secret"}}}', encoding="utf-8")
    original = server._is_reparse_point
    monkeypatch.setattr(server, "_is_reparse_point", lambda path: path == config or original(path))
    result = run(server.handle_mcp_audit({"cwd": str(tmp_path), "include_host": False}))
    assert result["status"] == "issues_found"
    assert result["server_count"] == 0
    assert "reparse point" in result["malformed"][0]["error"]


def test_package_inventory_requires_explicit_host_scope(tmp_path: Path):
    result = run(server.handle_ecosystem_scan({"cwd": str(tmp_path), "include_host": False, "include_packages": True}))
    assert result["status"] == "invalid_input"


def test_logs_query_filters_decisions_without_exposing_data_dir(tmp_path: Path, monkeypatch):
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
    result = run(server.handle_logs_query({"filter": "approvals", "last_n": 20}))
    assert result["matched"] == 2
    assert "data_dir" not in result


def test_mcp_audit_defaults_to_project_and_exposes_only_structural_metadata(tmp_path: Path):
    config = tmp_path / ".mcp.json"
    config.write_text(
        '{"mcpServers":{"example":{"command":"secret-cli --token secret","args":["server.py"],"env":{"TOKEN":"secret"}}}}',
        encoding="utf-8",
    )
    result = run(server.handle_mcp_audit({"cwd": str(tmp_path), "include_host": False}))
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
