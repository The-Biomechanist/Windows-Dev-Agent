"""Tool discovery must query version state through tool-appropriate authority surfaces."""

from __future__ import annotations

import asyncio

from src.mcp import server


def run(coro):
    return asyncio.run(coro)


def test_runtime_version_probes_use_documented_tool_argv(monkeypatch):
    resolved = {
        "python": r"C:\Tools\python.exe",
        "go": r"C:\Tools\go.exe",
        "java": r"C:\Tools\java.exe",
    }
    observed: list[list[str]] = []

    monkeypatch.setattr(server, "resolve_executable", lambda name: resolved.get(name))

    def fake_run(argv, **_kwargs):
        observed.append(argv)
        return {
            "succeeded": True,
            "stdout": "version-output",
            "stderr": "",
            "argv": argv,
            "execution_started": True,
            "output_capture_complete": True,
            "output_capture_settled": True,
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    monkeypatch.setattr(server, "run_bounded", fake_run)
    result = run(server.handle_tool_discover({"category": "runtimes"}))

    assert [r"C:\Tools\python.exe", "--version"] in observed
    assert [r"C:\Tools\go.exe", "version"] in observed
    assert [r"C:\Tools\java.exe", "-version"] in observed
    assert result["execution_started"] is True
    assert result["runtimes"]["go"]["version_source"] == "command"


def test_git_lfs_uses_version_subcommand(monkeypatch):
    observed = {}
    monkeypatch.setattr(server, "resolve_executable", lambda name: r"C:\Git\git-lfs.exe" if name == "git-lfs" else None)

    def fake_run(argv, **_kwargs):
        observed["argv"] = argv
        return {
            "succeeded": True,
            "stdout": "git-lfs/3.8.0",
            "stderr": "",
            "argv": argv,
            "execution_started": True,
            "output_capture_complete": True,
            "output_capture_settled": True,
            "stdout_truncated": False,
            "stderr_truncated": False,
        }

    monkeypatch.setattr(server, "run_bounded", fake_run)
    result = run(server.handle_tool_discover({"category": "vcs"}))

    assert observed["argv"] == [r"C:\Git\git-lfs.exe", "version"]
    assert result["vcs"]["git-lfs"]["version"] == "git-lfs/3.8.0"


def test_gui_editor_uses_windows_file_metadata_without_launch(monkeypatch):
    monkeypatch.setattr(server, "resolve_executable", lambda name: r"C:\VS\Common7\IDE\devenv.exe" if name == "devenv" else None)
    monkeypatch.setattr(server, "windows_file_version", lambda _path: "17.14.1234.0")
    monkeypatch.setattr(
        server,
        "run_bounded",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("GUI editor must not be launched for version metadata")),
    )

    result = run(server.handle_tool_discover({"category": "editors"}))
    devenv = result["editors"]["devenv"]

    assert devenv["available"] is True
    assert devenv["version"] == "17.14.1234.0"
    assert devenv["version_status"] == "known"
    assert devenv["version_source"] == "windows_file_version"
    assert result["execution_started"] is False


def test_missing_gui_file_version_remains_unknown_without_launch(monkeypatch):
    monkeypatch.setattr(server, "resolve_executable", lambda name: r"C:\JetBrains\rider64.exe" if name == "rider" else None)
    monkeypatch.setattr(server, "windows_file_version", lambda _path: None)
    monkeypatch.setattr(
        server,
        "run_bounded",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("GUI editor must not be launched for version metadata")),
    )

    result = run(server.handle_tool_discover({"category": "editors"}))
    rider = result["editors"]["rider"]

    assert rider["available"] is True
    assert rider["version"] is None
    assert rider["version_status"] == "unknown"
    assert rider["version_source"] == "windows_file_version"
    assert result["execution_started"] is False
