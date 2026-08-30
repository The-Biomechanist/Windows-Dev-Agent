"""Regression for the MCP-stdio / discovery-process boundary."""

import json

from src.discovery import discovery


def test_discovery_process_cannot_consume_mcp_stdin(monkeypatch):
    observed = {}

    class Result:
        returncode = 0
        stdout = json.dumps({})
        stderr = ""

    def fake_run(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return Result()

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    snapshot = discovery.EnvironmentDiscovery(cache_enabled=False)._run_discovery()

    assert snapshot is not None
    assert observed["stdin"] is discovery.subprocess.DEVNULL
    assert observed["argv"][0] == "powershell.exe"
