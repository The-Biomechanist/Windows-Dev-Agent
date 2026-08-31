"""Post-launch lifecycle observation failures must remain bounded, attributable receipts."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
import subprocess

from src import execution
from src.observability import trace


class _FakeProcess:
    pid = 4242

    def __init__(self, *, waits, polls):
        self.stdout = io.BytesIO(b"observed-before-lifecycle-error\n")
        self.stderr = io.BytesIO(b"")
        self._waits = iter(waits)
        self._polls = iter(polls)
        self.killed = False

    def wait(self, timeout=None):
        outcome = next(self._waits)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def poll(self):
        outcome = next(self._polls)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def kill(self):
        self.killed = True


def _run_with_process(monkeypatch, process: _FakeProcess, *, timeout: int = 10):
    monkeypatch.setattr(
        execution,
        "launch_bound",
        lambda argv, **_kwargs: {"process": process, "argv": argv, "execution_started": True},
    )
    return execution.run_bounded([str(Path("C:/Tools/fake.exe"))], timeout=timeout)


def _hook(tool_name: str, tool_input: dict, result: dict):
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "tool_response": {"content": [{"type": "text", "text": json.dumps(result)}]},
    }


def test_wait_error_recovered_by_poll_keeps_normal_success(monkeypatch):
    process = _FakeProcess(waits=[OSError("transient wait failure")], polls=[0])
    monkeypatch.setattr(
        execution,
        "_terminate_process_tree",
        lambda _process: (_ for _ in ()).throw(AssertionError("recovered exit state must not be terminated")),
    )

    result = _run_with_process(monkeypatch, process)

    assert result["execution_started"] is True
    assert result["succeeded"] is True
    assert result["returncode"] == 0
    assert "lifecycle_error" not in result
    assert result["output_capture_complete"] is True
    assert result["output_capture_settled"] is True
    assert result["stdout_truncated"] is False
    assert "observed-before-lifecycle-error" in result["stdout"]


def test_unrecovered_wait_error_returns_started_receipt_and_attempts_cleanup(monkeypatch):
    process = _FakeProcess(
        waits=[OSError("process handle lost"), -9],
        polls=[None],
    )
    cleanup = {"called": False}

    def fake_cleanup(candidate):
        assert candidate is process
        cleanup["called"] = True

    monkeypatch.setattr(execution, "_terminate_process_tree", fake_cleanup)
    result = _run_with_process(monkeypatch, process)

    assert cleanup["called"] is True
    assert result["execution_started"] is True
    assert result["succeeded"] is False
    assert result["returncode"] == -9
    assert "process handle lost" in result["lifecycle_error"]
    assert result["error"] == result["lifecycle_error"]
    assert result["output_capture_complete"] is True
    assert result["output_capture_settled"] is True
    assert "observed-before-lifecycle-error" in result["stdout"]


def test_unrecovered_wait_and_cleanup_observation_errors_do_not_escape(monkeypatch):
    process = _FakeProcess(
        waits=[OSError("initial wait failed"), OSError("cleanup wait failed")],
        polls=[None, None],
    )
    monkeypatch.setattr(execution, "_terminate_process_tree", lambda _process: None)

    result = _run_with_process(monkeypatch, process)

    assert result["execution_started"] is True
    assert result["succeeded"] is False
    assert result["returncode"] is None
    assert "initial wait failed" in result["lifecycle_error"]
    assert result["output_capture_settled"] is True


def test_timeout_cleanup_wait_error_remains_timeout_plus_lifecycle_uncertainty(monkeypatch):
    process = _FakeProcess(
        waits=[
            subprocess.TimeoutExpired(cmd="fake", timeout=1),
            OSError("wait after cleanup failed"),
        ],
        polls=[None],
    )
    monkeypatch.setattr(execution, "_terminate_process_tree", lambda _process: None)

    result = _run_with_process(monkeypatch, process, timeout=1)

    assert result["execution_started"] is True
    assert result["succeeded"] is False
    assert result["timed_out"] is True
    assert "timed out" in result["error"].lower()
    assert "wait after cleanup failed" in result["lifecycle_error"]


def test_cleanup_path_tolerates_broken_poll_and_still_attempts_kill(monkeypatch):
    process = _FakeProcess(
        waits=[],
        polls=[OSError("poll handle failed"), OSError("poll still failed")],
    )
    monkeypatch.setattr(execution, "resolve_windows_system_executable", lambda _name: None)

    execution._terminate_process_tree(process)

    assert process.killed is True


def test_mutating_lifecycle_error_audits_unknown_effect():
    payload = _hook(
        "mcp__windows-dev-agent__package_install",
        {"execute": True, "package_id": "Python.Python.3.12"},
        {
            "status": "failed",
            "succeeded": False,
            "execution_started": True,
            "lifecycle_error": "process lifecycle observation failed",
        },
    )
    assert trace.derive_execution_outcome(payload) == ("unknown", "failed")


def test_diagnostic_lifecycle_error_audits_unknown_execution_outcome():
    payload = _hook(
        "mcp__windows-dev-agent__package_search",
        {"query": "Python"},
        {
            "status": "failed",
            "succeeded": False,
            "execution_started": True,
            "lifecycle_error": "process lifecycle observation failed",
        },
    )
    assert trace.derive_execution_outcome(payload) == ("unknown", "failed")
