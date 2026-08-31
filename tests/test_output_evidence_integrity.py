"""Captured text becomes a fact only when the relevant stream is whole enough to support it."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys

import pytest

from src import execution
from src.discovery import discovery as discovery_module
from src.discovery.discovery import EnvironmentDiscovery
from src.mcp import server


def run(coro):
    return asyncio.run(coro)


def _receipt(
    *,
    stdout: str = "",
    stderr: str = "",
    succeeded: bool = True,
    complete: bool = True,
    settled: bool = True,
    stdout_truncated: bool = False,
    stderr_truncated: bool = False,
):
    return {
        "succeeded": succeeded,
        "returncode": 0 if succeeded else 1,
        "stdout": stdout,
        "stderr": stderr,
        "argv": ["C:\\Tools\\probe.exe"],
        "execution_started": True,
        "output_capture_complete": complete,
        "output_capture_settled": settled,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


def test_bounded_runner_reports_tail_truncation_without_confusing_it_with_eof():
    executable = str(Path(sys.executable).resolve())
    stdout_payload = "abcdefghijklmnopqrstuvwxyz"
    stderr_payload = "0123456789"
    code = (
        "import sys; "
        f"sys.stdout.write({stdout_payload!r}); sys.stdout.flush(); "
        f"sys.stderr.write({stderr_payload!r}); sys.stderr.flush()"
    )
    result = execution.run_bounded(
        [executable, "-I", "-c", code],
        timeout=10,
        stdout_bytes=8,
        stderr_bytes=4,
    )

    assert result["succeeded"] is True
    assert result["output_capture_complete"] is True
    assert result["output_capture_settled"] is True
    assert result["stdout_truncated"] is True
    assert result["stderr_truncated"] is True
    assert result["stdout"] == stdout_payload[-8:]
    assert result["stderr"] == stderr_payload[-4:]


def test_tail_buffer_does_not_claim_truncation_when_payload_exactly_fits():
    tail = execution._TailBuffer(4)
    tail.append(b"abcd")
    assert tail.text() == "abcd"
    assert tail.truncated is False

    tail.append(b"e")
    assert tail.text() == "bcde"
    assert tail.truncated is True

    zero = execution._TailBuffer(0)
    zero.append(b"x")
    assert zero.text() == ""
    assert zero.truncated is True


@pytest.mark.parametrize(
    ("complete", "settled", "truncated"),
    [(False, True, False), (True, False, False), (True, True, True)],
)
def test_command_tool_version_remains_unknown_without_complete_stream_evidence(
    monkeypatch, complete: bool, settled: bool, truncated: bool
):
    monkeypatch.setattr(server, "resolve_executable", lambda name: r"C:\Tools\git.exe" if name == "git" else None)
    monkeypatch.setattr(
        server,
        "run_bounded",
        lambda *_args, **_kwargs: _receipt(
            stdout="git version 9.9.9",
            complete=complete,
            settled=settled,
            stdout_truncated=truncated,
        ),
    )

    result = run(server.handle_tool_discover({"category": "vcs"}))
    git = result["vcs"]["git"]
    assert git["available"] is True
    assert git["version"] is None
    assert git["version_status"] == "unknown"


def test_stderr_version_can_be_known_when_that_stream_is_complete(monkeypatch):
    monkeypatch.setattr(server, "resolve_executable", lambda name: r"C:\Tools\java.exe" if name == "java" else None)
    monkeypatch.setattr(
        server,
        "run_bounded",
        lambda *_args, **_kwargs: _receipt(stderr='java version "99"'),
    )

    result = run(server.handle_tool_discover({"category": "runtimes"}))
    assert result["runtimes"]["java"]["version"] == 'java version "99"'


@pytest.mark.parametrize(
    ("complete", "truncated"),
    [(False, False), (True, True)],
)
def test_canonical_discovery_rejects_even_valid_json_from_incomplete_stdout(
    monkeypatch, tmp_path: Path, complete: bool, truncated: bool
):
    valid = json.dumps(
        {
            "timestamp": datetime.now().isoformat(),
            "success": True,
            "errors": [],
        }
    )
    monkeypatch.setattr(
        discovery_module,
        "_system_powershell",
        lambda: Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"),
    )
    monkeypatch.setattr(
        discovery_module,
        "run_bounded",
        lambda *_args, **_kwargs: _receipt(
            stdout=valid,
            complete=complete,
            stdout_truncated=truncated,
        ),
    )

    snapshot = EnvironmentDiscovery(cache_enabled=False, data_dir=tmp_path).discover()
    assert snapshot.success is False
    assert any("incomplete or truncated" in error for error in snapshot.errors)


def test_ecosystem_inventory_does_not_promote_partial_command_output(monkeypatch, tmp_path: Path):
    def resolve(name: str):
        if name == "code":
            return r"C:\Tools\code.exe"
        if name == "winget":
            return r"C:\Tools\winget.exe"
        return None

    def fake_run(argv, **_kwargs):
        if "--list-extensions" in argv:
            return _receipt(stdout="publisher.extension\n", complete=False)
        if "list" in argv:
            return _receipt(stdout="Package One\nPackage Two\n", stdout_truncated=True)
        raise AssertionError(argv)

    monkeypatch.setattr(server, "resolve_executable", resolve)
    monkeypatch.setattr(server, "run_bounded", fake_run)

    result = run(
        server.handle_ecosystem_scan(
            {
                "cwd": str(tmp_path),
                "include_host": True,
                "include_packages": True,
            }
        )
    )
    inventory = result["inventory"]
    assert inventory["vscode"]["installed"] == []
    assert inventory["packages"]["items"] == []
    assert "VS Code extension inventory output was incomplete or truncated" in inventory["warnings"]
    assert "winget list output was incomplete or truncated" in inventory["warnings"]


@pytest.mark.parametrize(
    ("complete", "truncated"),
    [(False, False), (True, True)],
)
def test_successful_package_search_reports_incomplete_when_result_stream_is_not_whole(
    monkeypatch, complete: bool, truncated: bool
):
    monkeypatch.setattr(server, "resolve_executable", lambda name: r"C:\Tools\winget.exe" if name == "winget" else None)
    monkeypatch.setattr(
        server,
        "run_bounded",
        lambda *_args, **_kwargs: _receipt(
            stdout="Python.Python.3.12",
            complete=complete,
            stdout_truncated=truncated,
        ),
    )

    result = run(server.handle_package_search({"query": "Python", "source": "winget"}))
    assert result["succeeded"] is True
    assert result["status"] == "incomplete"
    assert "incomplete or truncated" in result["warning"]
