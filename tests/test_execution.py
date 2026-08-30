"""Contract tests for bounded identity-preserving subprocess execution."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

from src.execution import resolve_executable, resolve_windows_system_executable, run_bounded


def test_resolve_executable_returns_absolute_identity():
    resolved = resolve_executable(sys.executable)
    assert resolved is not None
    assert Path(resolved).is_absolute()
    assert Path(resolved).resolve() == Path(sys.executable).resolve()


def test_runner_rejects_unresolved_executable_identity():
    result = run_bounded(["python", "--version"])
    assert result["succeeded"] is False
    assert result["execution_started"] is False
    assert "absolute path" in result["error"]


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


@pytest.mark.skipif(os.name != "nt", reason="trusted Windows system resolution is Windows-specific")
def test_windows_control_plane_resolution_avoids_path():
    resolved = resolve_windows_system_executable("cmd.exe")
    assert resolved is not None
    path = Path(resolved)
    assert path.is_absolute()
    assert path.name.lower() == "cmd.exe"
    assert path.parent.name.lower() in {"system32", "sysnative"}
