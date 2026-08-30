"""Regression coverage for diagnostic external-process accounting."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.discovery import discovery as discovery_module
from src.discovery.discovery import EnvironmentDiscovery
from src.mcp import server
from src.observability import audit_report, trace


def run(coro):
    return asyncio.run(coro)


def test_discovery_cache_hit_does_not_claim_external_execution(tmp_path: Path):
    writer = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path)
    writer._save_cache(writer._fallback_discovery("cached"))
    reader = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path)
    reader.discover()
    assert reader.last_execution_started is False


def test_native_discovery_records_that_powershell_started(tmp_path: Path, monkeypatch):
    script = tmp_path / "discovery.ps1"
    script.write_text("# test", encoding="utf-8")
    powershell = tmp_path / "powershell.exe"
    powershell.write_bytes(b"")
    probe = EnvironmentDiscovery(cache_enabled=False, data_dir=tmp_path)
    payload = probe._fallback_discovery("synthetic").to_dict()

    class Result:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(discovery_module, "DISCOVERY_SCRIPT", script)
    monkeypatch.setattr(discovery_module, "_system_powershell", lambda: powershell)
    monkeypatch.setattr(discovery_module.subprocess, "run", lambda *_args, **_kwargs: Result())
    probe.discover(force_refresh=True)
    assert probe.last_execution_started is True


def test_tool_discover_reports_observed_process_start(monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda name: "python.exe" if name == "python" else None)
    monkeypatch.setattr(
        server,
        "_run",
        lambda argv, **_kwargs: {
            "succeeded": True,
            "stdout": "Python 3.13",
            "stderr": "",
            "argv": argv,
            "execution_started": True,
        },
    )
    result = run(server.handle_tool_discover({"category": "runtimes"}))
    assert result["execution_started"] is True
    assert result["runtimes"]["python"]["version_status"] == "known"


def test_tool_discover_without_available_tools_reports_no_process_start(monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda _name: None)
    result = run(server.handle_tool_discover({"category": "vcs"}))
    assert result["execution_started"] is False


def test_project_only_ecosystem_scan_reports_no_external_process(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        server.shutil,
        "which",
        lambda _name: (_ for _ in ()).throw(AssertionError("host lookup should not run")),
    )
    result = run(server.handle_ecosystem_scan({"cwd": str(tmp_path), "include_host": False}))
    assert result["execution_started"] is False


def test_host_ecosystem_scan_reports_observed_process_start(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server.shutil, "which", lambda name: "code.exe" if name == "code" else None)
    monkeypatch.setattr(
        server,
        "_run",
        lambda argv, **_kwargs: {
            "succeeded": True,
            "stdout": "ms-python.python\n",
            "stderr": "",
            "argv": argv,
            "execution_started": True,
        },
    )
    result = run(server.handle_ecosystem_scan({"cwd": str(tmp_path), "include_host": True}))
    assert result["execution_started"] is True


def test_trace_persists_runtime_process_start_evidence():
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "mcp__windows-dev-agent__tool_discover",
        "tool_input": {"category": "runtimes"},
        "tool_response": {
            "content": [
                {"type": "text", "text": '{"execution_started":true,"runtimes":{}}'}
            ]
        },
    }
    event = trace.event_from_hook(payload)
    assert event["execution_started"] is True
    assert event["execution_outcome"] == "not_applicable"


def test_nonexecuting_plan_records_false_process_start():
    payload = {
        "hook_event_name": "PostToolUse",
        "tool_name": "mcp__windows-dev-agent__package_install",
        "tool_input": {"package_id": "Python.Python.3.13", "execute": False},
        "tool_response": {
            "content": [{"type": "text", "text": '{"status":"planned"}'}]
        },
    }
    assert trace.derive_execution_started(payload) is False


def test_audit_summary_counts_process_start_independently_of_effect_outcome():
    summary = audit_report.summarize(
        [
            {
                "event": "PostToolUse",
                "execution_started": True,
                "execution_outcome": "not_applicable",
            }
        ]
    )
    assert summary["external_process_started"] == 1
    assert summary["not_applicable"] == 1
