"""Windows command-target regressions for PowerShell shims and batch-file rejection."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from src import execution
from src.file_guard import (
    EXECUTABLE_IDENTITY_POWERSHELL_SCRIPT,
    executable_identity,
)


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows command-target contract")


def test_windows_resolution_prefers_native_then_powershell_and_never_batch(tmp_path: Path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    cmd = tools / "shim.cmd"
    script = tools / "shim.ps1"
    cmd.write_text("@echo off\r\n", encoding="ascii")
    script.write_text("$args | Out-Null\n", encoding="utf-8")

    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setenv("PATHEXT", ".CMD;.BAT;.EXE;.COM")

    resolved = execution.resolve_executable("shim")
    assert resolved is not None
    assert Path(resolved).resolve() == script.resolve()
    assert executable_identity(resolved).kind == EXECUTABLE_IDENTITY_POWERSHELL_SCRIPT
    assert execution.resolve_executable(str(cmd)) is None

    native = tools / "shim.exe"
    native.write_bytes(Path(sys.executable).read_bytes())
    assert Path(execution.resolve_executable("shim")).resolve() == native.resolve()

    native.unlink()
    script.unlink()
    assert execution.resolve_executable("shim") is None


def test_powershell_shim_preserves_shell_metacharacters_as_argument_data(tmp_path: Path, monkeypatch):
    tools = tmp_path / "tools"
    tools.mkdir()
    injection_marker = tmp_path / "cmd-injected.txt"
    capture = tmp_path / "captured.txt"

    (tools / "shim.cmd").write_text(
        f"@echo off\r\necho batch-path>\"{injection_marker}\"\r\n",
        encoding="ascii",
    )
    (tools / "shim.ps1").write_text(
        "param([string]$OutPath)\n"
        "[IO.File]::WriteAllText($OutPath, [string]$args[0])\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PATH", str(tools))
    monkeypatch.setenv("PATHEXT", ".CMD;.BAT;.EXE;.COM")

    resolved = execution.resolve_executable("shim")
    assert resolved is not None and resolved.lower().endswith(".ps1")
    dangerous = f'& echo WDA_INJECTED > "{injection_marker}" | %PATH% ^ ! "quoted value"'
    result = execution.run_bounded([resolved, str(capture), dangerous], timeout=20)

    assert result["succeeded"] is True, result
    assert result["execution_started"] is True
    assert capture.read_text(encoding="utf-8") == dangerous
    assert not injection_marker.exists()


def test_direct_batch_target_fails_before_process_start(tmp_path: Path, monkeypatch):
    batch = tmp_path / "unsafe.cmd"
    batch.write_text("@echo off\r\n", encoding="ascii")
    monkeypatch.setattr(
        execution.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("batch target must not start")),
    )

    result = execution.launch_bound([str(batch), "&", "echo", "bad"])

    assert result["succeeded"] is False
    assert result["execution_started"] is False
    assert "Unsupported Windows command target" in result["error"]
    assert executable_identity(batch) is None


def test_powershell_target_and_interpreter_are_locked_through_process_creation(tmp_path: Path, monkeypatch):
    script = tmp_path / "shim.ps1"
    script.write_text("$args | Out-Null\n", encoding="utf-8")
    interpreter = tmp_path / "powershell.exe"
    interpreter.write_bytes(Path(sys.executable).read_bytes())
    observed = {"script_write_blocked": False, "interpreter_write_blocked": False, "argv": None}

    class FakeProcess:
        pid = 42

    monkeypatch.setattr(execution, "resolve_windows_system_executable", lambda *_names: str(interpreter))

    def fake_popen(argv, **_kwargs):
        observed["argv"] = list(argv)
        try:
            script.write_text("changed\n", encoding="utf-8")
        except OSError:
            observed["script_write_blocked"] = True
        try:
            interpreter.write_bytes(b"changed")
        except OSError:
            observed["interpreter_write_blocked"] = True
        return FakeProcess()

    monkeypatch.setattr(execution.subprocess, "Popen", fake_popen)
    result = execution.launch_bound([str(script), "literal&argument"])

    assert result["execution_started"] is True
    assert observed["script_write_blocked"] is True
    assert observed["interpreter_write_blocked"] is True
    assert observed["argv"] == [
        str(interpreter),
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(script),
        "literal&argument",
    ]
