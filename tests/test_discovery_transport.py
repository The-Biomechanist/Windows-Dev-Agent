"""Regressions for the MCP-stdio / discovery-process trust boundary."""

import json
from pathlib import Path

from src.discovery import discovery


def test_discovery_process_uses_system_powershell_and_cannot_consume_mcp_stdin(monkeypatch):
    observed = {}
    trusted = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")

    class Result:
        returncode = 0
        stdout = json.dumps({})
        stderr = ""

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return Result()

    monkeypatch.setattr(discovery, "_system_powershell", lambda: trusted)
    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    snapshot = discovery.EnvironmentDiscovery(cache_enabled=False)._run_discovery()

    assert snapshot is not None
    assert observed["stdin"] is discovery.subprocess.DEVNULL
    assert observed["argv"][0] == str(trusted)
    assert observed["timeout"] == discovery.DISCOVERY_TIMEOUT_SECONDS


def test_missing_system_powershell_degrades_without_path_execution(monkeypatch):
    monkeypatch.setattr(discovery, "_system_powershell", lambda: None)
    monkeypatch.setattr(
        discovery.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("PATH process execution should not occur")),
    )
    snapshot = discovery.EnvironmentDiscovery(cache_enabled=False)._run_discovery()
    assert snapshot.success is False
    assert "System Windows PowerShell" in snapshot.errors[0]
