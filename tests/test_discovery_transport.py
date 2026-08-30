"""Regressions for the MCP-stdio / discovery-process trust boundary."""

from pathlib import Path

from src.discovery import discovery


def test_discovery_process_uses_system_powershell_through_shared_bounded_runner(monkeypatch):
    observed = {}
    trusted = Path(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")

    def fake_run_bounded(argv, **kwargs):
        observed["argv"] = argv
        observed.update(kwargs)
        return {
            "succeeded": True,
            "returncode": 0,
            "stdout": "{}",
            "stderr": "",
            "execution_started": True,
        }

    monkeypatch.setattr(discovery, "_system_powershell", lambda: trusted)
    monkeypatch.setattr(discovery, "run_bounded", fake_run_bounded)
    snapshot = discovery.EnvironmentDiscovery(cache_enabled=False)._run_discovery()

    assert snapshot is not None
    assert observed["argv"][0] == str(trusted)
    assert observed["timeout"] == discovery.DISCOVERY_TIMEOUT_SECONDS
    assert observed["stdout_bytes"] == discovery.MAX_DISCOVERY_STDOUT_BYTES
    assert observed["stderr_bytes"] == discovery.MAX_DISCOVERY_STDERR_BYTES


def test_missing_system_powershell_degrades_without_process_execution(monkeypatch):
    monkeypatch.setattr(discovery, "_system_powershell", lambda: None)
    monkeypatch.setattr(
        discovery,
        "run_bounded",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("process execution should not occur")),
    )
    snapshot = discovery.EnvironmentDiscovery(cache_enabled=False)._run_discovery()
    assert snapshot.success is False
    assert "System Windows PowerShell" in snapshot.errors[0]
