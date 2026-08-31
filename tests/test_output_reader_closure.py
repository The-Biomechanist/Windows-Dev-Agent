"""Bounded execution must settle output readers before publishing a receipt."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from src import execution


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows synchronous pipe-cancellation contract")

_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_STILL_ACTIVE = 259


def _new_wda_reader_threads(before: set[int | None]) -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.ident not in before
        and thread.is_alive()
        and thread.name.startswith(("wda-stdout-drain-", "wda-stderr-drain-"))
    ]


def _open_process(access: int, pid: int):
    kernel32 = ctypes.windll.kernel32
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    open_process.restype = ctypes.c_void_p
    return open_process(access, False, pid)


def _pid_is_alive(pid: int) -> bool:
    handle = _open_process(_PROCESS_QUERY_LIMITED_INFORMATION, pid)
    if not handle:
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        get_exit_code = kernel32.GetExitCodeProcess
        get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
        get_exit_code.restype = ctypes.c_int
        code = ctypes.c_uint32()
        return bool(get_exit_code(handle, ctypes.byref(code))) and code.value == _STILL_ACTIVE
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _terminate_pid(pid: int) -> None:
    handle = _open_process(_PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED_INFORMATION, pid)
    if handle:
        try:
            terminate = ctypes.windll.kernel32.TerminateProcess
            terminate.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            terminate.restype = ctypes.c_int
            terminate(handle, 1)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and _pid_is_alive(pid):
        time.sleep(0.05)


def test_normal_process_reaches_clean_output_eof():
    executable = str(Path(sys.executable).resolve())
    result = execution.run_bounded(
        [executable, "-I", "-c", "print('clean-output')"],
        timeout=10,
    )

    assert result["succeeded"] is True
    assert result["execution_started"] is True
    assert result["output_capture_complete"] is True
    assert result["output_capture_settled"] is True
    assert "clean-output" in result["stdout"]


def test_surviving_descendant_cannot_leave_reader_threads_live_after_timeout(tmp_path: Path, monkeypatch):
    executable = str(Path(sys.executable).resolve())
    pid_file = tmp_path / "pids.txt"
    parent_code = (
        "import os, pathlib, subprocess, sys, time; "
        "child=subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(30)'], "
        "stdin=subprocess.DEVNULL, stdout=sys.stdout, stderr=sys.stderr); "
        "pathlib.Path(sys.argv[1]).write_text(f'{os.getpid()},{child.pid}', encoding='ascii'); "
        "print('parent-ready', flush=True); "
        "time.sleep(30)"
    )

    def kill_parent_only(process) -> None:
        process.kill()

    monkeypatch.setattr(execution, "_terminate_process_tree", kill_parent_only)
    before = {thread.ident for thread in threading.enumerate()}
    child_pid: int | None = None
    parent_pid: int | None = None
    try:
        result = execution.run_bounded(
            [executable, "-I", "-c", parent_code, str(pid_file)],
            timeout=2,
        )
        assert pid_file.is_file(), result
        parent_raw, child_raw = pid_file.read_text(encoding="ascii").split(",", 1)
        parent_pid = int(parent_raw)
        child_pid = int(child_raw)

        assert result["succeeded"] is False
        assert result["timed_out"] is True
        assert result["execution_started"] is True
        assert result["output_capture_complete"] is False
        assert result["output_capture_settled"] is True
        assert "parent-ready" in result["stdout"]
        assert not [
            thread
            for thread in _new_wda_reader_threads(before)
            if thread.name.endswith(f"-{parent_pid}")
        ]

        # The descendant was deliberately left alive by the mocked cleanup path;
        # output settlement must not depend on that process exiting.
        assert _pid_is_alive(child_pid) is True
    finally:
        if child_pid is not None:
            _terminate_pid(child_pid)
