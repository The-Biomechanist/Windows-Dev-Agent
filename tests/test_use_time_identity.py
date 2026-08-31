"""Regressions for verification-to-use identity and containment."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from src import execution
from src.file_guard import executable_identity
from src.mcp import server


def run(coro):
    return asyncio.run(coro)


def test_same_path_content_change_is_rejected_before_process_start(tmp_path: Path):
    executable = tmp_path / "tool.exe"
    executable.write_bytes(b"reviewed-bytes")
    reviewed = executable_identity(executable)
    assert reviewed is not None and reviewed.kind == "file"
    executable.write_bytes(b"changed-bytes")

    result = execution.run_bounded(
        [str(executable)],
        expected_executable_identity_kind=reviewed.kind,
        expected_executable_identity_sha256=reviewed.sha256,
    )

    assert result["succeeded"] is False
    assert result["execution_started"] is False
    assert result["identity_mismatch"] is True


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing contract is Windows-specific")
def test_verified_executable_is_write_delete_locked_through_process_creation(tmp_path: Path, monkeypatch):
    executable = tmp_path / "tool.exe"
    executable.write_bytes(b"reviewed-bytes")
    reviewed = executable_identity(executable)
    assert reviewed is not None and reviewed.kind == "file"
    observed = {"write_blocked": False, "replace_blocked": False}

    class FakeProcess:
        pid = 42

    def fake_popen(*_args, **_kwargs):
        try:
            with executable.open("wb") as handle:
                handle.write(b"changed")
        except PermissionError:
            observed["write_blocked"] = True

        replacement = tmp_path / "replacement.exe"
        replacement.write_bytes(b"replacement")
        try:
            os.replace(replacement, executable)
        except PermissionError:
            observed["replace_blocked"] = True
        return FakeProcess()

    monkeypatch.setattr(execution.subprocess, "Popen", fake_popen)
    result = execution.launch_bound(
        [str(executable)],
        expected_executable_identity_kind=reviewed.kind,
        expected_executable_identity_sha256=reviewed.sha256,
    )

    assert result["execution_started"] is True
    assert observed == {"write_blocked": True, "replace_blocked": True}


@pytest.mark.skipif(os.name != "nt", reason="App Execution Alias is Windows-specific")
def test_app_execution_alias_identity_is_sealed_through_launch():
    winget = execution.resolve_executable("winget")
    if not winget or "windowsapps" not in winget.lower():
        pytest.skip("WinGet App Execution Alias is not exposed on this host")
    reviewed = executable_identity(winget)
    assert reviewed is not None
    assert reviewed.kind == "app_execution_alias"
    assert len(reviewed.sha256) == 64

    result = execution.run_bounded(
        [winget, "--version"],
        timeout=20,
        expected_executable_identity_kind=reviewed.kind,
        expected_executable_identity_sha256=reviewed.sha256,
    )

    assert result["succeeded"] is True
    assert result["execution_started"] is True
    assert result["stdout"].strip().startswith("v")


def test_sandbox_staging_rechecks_byte_budget_after_initial_validation(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = workspace / "artifact.bin"
    payload.write_bytes(b"x")
    sources, error = server._payload_sources(workspace, ["artifact.bin"])
    assert error is None and sources is not None

    payload.write_bytes(b"12345")
    monkeypatch.setattr(server, "MAX_SANDBOX_PAYLOAD_BYTES", 4)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path / "data")

    with pytest.raises(RuntimeError, match="staging byte budget"):
        server._prepare_windows_sandbox(
            "type artifact.bin",
            workspace,
            sources,
            r"C:\Windows\System32\WindowsSandbox.exe",
        )
    run_root = tmp_path / "data" / "sandbox-runs"
    assert not run_root.exists() or not any(run_root.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="NTFS junction contract is Windows-specific")
def test_sandbox_staging_rejects_directory_swapped_to_junction_after_validation(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    selected = workspace / "bundle"
    selected.mkdir()
    (selected / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")

    sources, error = server._payload_sources(workspace, ["bundle"])
    assert error is None and sources is not None

    shutil.rmtree(selected)
    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(selected), str(outside)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable on runner: {created.stderr or created.stdout}")

    monkeypatch.setattr(server, "DATA_DIR", tmp_path / "data")
    try:
        with pytest.raises(RuntimeError, match="reparse point"):
            server._prepare_windows_sandbox(
                "type bundle\\secret.txt",
                workspace,
                sources,
                r"C:\Windows\System32\WindowsSandbox.exe",
            )
    finally:
        try:
            os.rmdir(selected)
        except OSError:
            pass


def test_project_json_consumes_guarded_handle_after_precheck(tmp_path: Path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text('{"ok":true}', encoding="utf-8")
    observed = {}
    original = server.guarded_open_read

    def guarded(path, **kwargs):
        observed.update(kwargs)
        return original(path, **kwargs)

    monkeypatch.setattr(server, "guarded_open_read", guarded)
    data, error = server._safe_json(config, project_root=tmp_path)

    assert error is None
    assert data == {"ok": True}
    assert observed["root"] == tmp_path
    assert observed["exact_path"] is True


@pytest.mark.skipif(os.name != "nt", reason="NTFS junction contract is Windows-specific")
def test_project_json_rejects_parent_swapped_to_junction_between_check_and_open(tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    config_dir = project / "config"
    config_dir.mkdir()
    config = config_dir / "settings.json"
    config.write_text('{"inside":true}', encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "settings.json").write_text('{"secret":true}', encoding="utf-8")

    original_status = server._project_path_status
    swapped = False

    def status_then_swap(root: Path, path: Path):
        nonlocal swapped
        result = original_status(root, path)
        if path == config and result == (True, None) and not swapped:
            shutil.rmtree(config_dir)
            created = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(config_dir), str(outside)],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if created.returncode != 0:
                pytest.skip(f"junction creation unavailable on runner: {created.stderr or created.stdout}")
            swapped = True
        return result

    monkeypatch.setattr(server, "_project_path_status", status_then_swap)
    try:
        data, error = server._safe_json(config, project_root=project)
        assert data is None
        assert error is not None
        assert "boundary" in error.lower() or "reparse" in error.lower()
    finally:
        if swapped:
            try:
                os.rmdir(config_dir)
            except OSError:
                pass
