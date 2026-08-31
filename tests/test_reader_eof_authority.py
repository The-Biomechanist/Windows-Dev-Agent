"""A settled output reader is complete only when it actually observed EOF."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from src import execution


class _FailingStream:
    def __init__(self, error: BaseException):
        self._error = error
        self._reads = 0
        self.closed = False

    def read1(self, _size: int) -> bytes:
        self._reads += 1
        if self._reads == 1:
            return b"partial-before-read-error\n"
        raise self._error

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    pid = 7331

    def __init__(self, stdout, stderr):
        self.stdout = stdout
        self.stderr = stderr

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0


@pytest.mark.parametrize("error", [OSError("pipe read failed"), ValueError("stream state failed")])
def test_reader_error_is_settled_but_not_complete(monkeypatch, error: BaseException):
    stdout = _FailingStream(error)
    process = _FakeProcess(stdout, io.BytesIO(b""))
    monkeypatch.setattr(
        execution,
        "launch_bound",
        lambda argv, **_kwargs: {"process": process, "argv": argv, "execution_started": True},
    )

    result = execution.run_bounded([str(Path("C:/Tools/fake.exe"))], timeout=10)

    assert result["succeeded"] is True
    assert result["execution_started"] is True
    assert result["output_capture_settled"] is True
    assert result["output_capture_complete"] is False
    assert result["stdout_truncated"] is False
    assert "partial-before-read-error" in result["stdout"]
    assert stdout.closed is True


def test_stderr_reader_error_makes_joint_capture_incomplete(monkeypatch):
    stderr = _FailingStream(OSError("stderr pipe failed"))
    process = _FakeProcess(io.BytesIO(b"complete-stdout\n"), stderr)
    monkeypatch.setattr(
        execution,
        "launch_bound",
        lambda argv, **_kwargs: {"process": process, "argv": argv, "execution_started": True},
    )

    result = execution.run_bounded([str(Path("C:/Tools/fake.exe"))], timeout=10)

    assert result["succeeded"] is True
    assert result["output_capture_settled"] is True
    assert result["output_capture_complete"] is False
    assert result["stdout"] == "complete-stdout\n"
    assert "partial-before-read-error" in result["stderr"]


def test_clean_eof_still_reports_complete(monkeypatch):
    process = _FakeProcess(io.BytesIO(b"stdout\n"), io.BytesIO(b"stderr\n"))
    monkeypatch.setattr(
        execution,
        "launch_bound",
        lambda argv, **_kwargs: {"process": process, "argv": argv, "execution_started": True},
    )

    result = execution.run_bounded([str(Path("C:/Tools/fake.exe"))], timeout=10)

    assert result["succeeded"] is True
    assert result["output_capture_complete"] is True
    assert result["output_capture_settled"] is True


def test_reader_state_cardinality_mismatch_is_not_silently_accepted():
    with pytest.raises(ValueError, match="cardinality"):
        execution._settle_output_readers([], [execution._DrainState()])
