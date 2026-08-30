"""Runtime contracts for the MCP audit-history query surface."""

import asyncio
import json
from pathlib import Path

from src.mcp import server


def run(coro):
    return asyncio.run(coro)


def test_logs_query_reads_retained_rotated_and_current_history(tmp_path: Path, monkeypatch):
    log = tmp_path / "agent.log"
    backup = tmp_path / "agent.log.1"
    backup.write_text(
        json.dumps({"tool_name": "older", "execution_outcome": "failed"}) + "\n",
        encoding="utf-8",
    )
    log.write_text(
        json.dumps({"tool_name": "newer", "execution_outcome": "succeeded"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "LOG_FILE", log)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)

    result = run(server.handle_logs_query({"filter": "all", "last_n": 20}))

    assert result["matched"] == 2
    assert [event["tool_name"] for event in result["events"]] == ["older", "newer"]


def test_logs_query_failure_filter_includes_retained_predecessor(tmp_path: Path, monkeypatch):
    log = tmp_path / "agent.log"
    backup = tmp_path / "agent.log.1"
    backup.write_text(
        json.dumps({"tool_name": "package_search", "execution_outcome": "failed"}) + "\n",
        encoding="utf-8",
    )
    log.write_text(
        json.dumps({"tool_name": "package_install", "execution_outcome": "succeeded"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "LOG_FILE", log)
    monkeypatch.setattr(server, "DATA_DIR", tmp_path)

    result = run(server.handle_logs_query({"filter": "failures", "last_n": 20}))

    assert result["matched"] == 1
    assert result["events"][0]["tool_name"] == "package_search"
