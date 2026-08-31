"""Contract tests for bounded identity-preserving subprocess execution."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from src import execution as execution_module
from src.execution import (
    executable_identity_matches,
    launch_bound,
    resolve_executable,
    resolve_windows_system_executable,
    run_bounded,
)


def test_resolve_executable_returns_absolute_identity():
    resolved = resolve_executable(sys.executable)
    assert resolved is not None
    assert Path(resolved).is_absolute()
    assert Path(resolved).resolve() == Path(sys.executable).resolve()


def test_reviewed_executable_identity_requires_same_live_absolute_file(tmp_path: Path):
    executable = tmp_path / "tool.exe"
    executable.write_bytes(b"tool")
    other = tmp_path / "other.exe"
    other.write_bytes(b"other")

    assert executable_identity_matches(str(executable), str(executable)) is True
    assert executable_identity_matches(str(executable), str(other)) is False
    assert executable_identity_matches("tool.exe", str(executable)) is False
    assert executable_identity_matches(str(tmp_path / "missing.exe"), str(executable)) is False


def test_runner_rejects_unresolved_executable_identity():
    result = run_bounded(["python", "--version"])
    assert result["succeeded"] is False
    assert result["execution_started"] is False
    assert "absolute path" in result["error"]


def test_unreviewed_launch_fails_closed_when_current_identity_is_unavailable(monkeypatch):
    executable = str(Path(sys.executable).resolve())
    monkeypatch.setattr(execution_module, "executable_identity", lambda _path: None)
    monkeypatch.setattr(
        execution_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("identity-less launch must not start")),
    )

    result = launch_bound([executable, "--version"])

    assert result["succeeded"] is False
    assert result["execution_started"] is False
    assert result["identity_unavailable"] is True


def test_runner_cannot_consume_parent_stdin_and_captures_bounded_output():
    executable = str(Path(sys.executable).resolve())
    result = run_bounded(
        [executable, "-I", "-c", "import sys; print(sys.stdin.read()); print('x'*20000)"],
        timeout=10,
        stdout_bytes=1024,
        stderr_bytes=512,
    )
    assert result["succeeded"] is True
    assert result["execution_started"] is True
    assert len(result["stdout"].encode("utf-8")) <= 1024
    assert result["stdout"].rstrip().endswith("x" * min(1000, len(result["stdout"].rstrip())))


@pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing identity guard is Windows-specific")
def test_unreviewed_launch_holds_current_file_identity_through_process_creation(tmp_path: Path, monkeypatch):
    source = Path(sys.executable).resolve()
    executable = tmp_path / "python.exe"
    executable.write_bytes(source.read_bytes())
    observed = {"write_blocked": False, "replace_blocked": False}

    class FakeProcess:
        pid = 42

    def fake_popen(*_args, **_kwargs):
        try:
            executable.write_bytes(b"replacement")
        except OSError:
            observed["write_blocked"] = True

        replacement = tmp_path / "replacement.exe"
        replacement.write_bytes(b"replacement")
        try:
            os.replace(replacement, executable)
        except OSError:
            observed["replace_blocked"] = True
        return FakeProcess()

    monkeypatch.setattr(execution_module.subprocess, "Popen", fake_popen)
    result = launch_bound([str(executable)])

    assert result["execution_started"] is True
    assert observed == {"write_blocked": True, "replace_blocked": True}


@pytest.mark.skipif(os.name != "nt", reason="Windows App Execution Alias contract is Windows-specific")
def test_unreviewed_winget_alias_is_self_sealed_and_launches():
    executable = resolve_executable("winget")
    if not executable:
        pytest.skip("WinGet is unavailable on this Windows host")

    result = run_bounded([executable, "--version"], timeout=10)

    assert result["execution_started"] is True
    assert result["succeeded"] is True
    assert result.get("stdout") or result.get("stderr")


@pytest.mark.skipif(os.name != "nt", reason="trusted Windows system resolution is Windows-specific")
def test_windows_control_plane_resolution_avoids_path():
    resolved = resolve_windows_system_executable("cmd.exe")
    assert resolved is not None
    path = Path(resolved)
    assert path.is_absolute()
    assert path.name.lower() == "cmd.exe"
    assert path.parent.name.lower() in {"system32", "sysnative"}
