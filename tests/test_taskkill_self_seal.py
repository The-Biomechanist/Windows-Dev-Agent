"""Timeout cleanup must seal Windows taskkill.exe through process creation."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from src import execution


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows process-tree cleanup contract")


class _TargetProcess:
    pid = 4242

    def __init__(self) -> None:
        self.killed = False

    def poll(self):
        return -9 if self.killed else None

    def kill(self) -> None:
        self.killed = True


def test_taskkill_is_identity_locked_through_cleanup_process_creation(tmp_path: Path, monkeypatch):
    taskkill = tmp_path / "taskkill.exe"
    taskkill.write_bytes(Path(sys.executable).read_bytes())
    replacement = tmp_path / "replacement.exe"
    replacement.write_bytes(b"replacement")
    target = _TargetProcess()
    observed = {"write_blocked": False, "replace_blocked": False, "argv": None}

    class CleanupProcess:
        def wait(self, timeout=None):
            return 0

        def kill(self):
            raise AssertionError("successful cleanup helper must not be killed")

    def fake_popen(argv, **_kwargs):
        observed["argv"] = list(argv)
        try:
            taskkill.write_bytes(b"changed")
        except OSError:
            observed["write_blocked"] = True
        try:
            os.replace(replacement, taskkill)
        except OSError:
            observed["replace_blocked"] = True
        return CleanupProcess()

    monkeypatch.setattr(execution, "resolve_windows_system_executable", lambda name: str(taskkill) if name == "taskkill.exe" else None)
    monkeypatch.setattr(execution.subprocess, "Popen", fake_popen)

    execution._terminate_process_tree(target)

    assert observed["argv"] == [str(taskkill), "/PID", str(target.pid), "/T", "/F"]
    assert observed["write_blocked"] is True
    assert observed["replace_blocked"] is True
    assert target.killed is True


def test_hung_taskkill_is_bounded_and_original_process_still_gets_fallback_kill(tmp_path: Path, monkeypatch):
    taskkill = tmp_path / "taskkill.exe"
    taskkill.write_bytes(Path(sys.executable).read_bytes())
    target = _TargetProcess()
    cleanup_state = {"killed": False, "waits": 0}

    class HungCleanupProcess:
        def wait(self, timeout=None):
            cleanup_state["waits"] += 1
            if cleanup_state["waits"] == 1:
                raise subprocess.TimeoutExpired(cmd="taskkill", timeout=timeout)
            return -9

        def kill(self):
            cleanup_state["killed"] = True

    monkeypatch.setattr(execution, "resolve_windows_system_executable", lambda name: str(taskkill) if name == "taskkill.exe" else None)
    monkeypatch.setattr(execution.subprocess, "Popen", lambda *_args, **_kwargs: HungCleanupProcess())

    execution._terminate_process_tree(target)

    assert cleanup_state == {"killed": True, "waits": 2}
    assert target.killed is True
