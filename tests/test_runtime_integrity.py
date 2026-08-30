"""Regression tests for post-0.4 runtime integrity seams."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess

import pytest

from src.capabilities import Capability, CapabilityTool
from src.mcp import server


def run(coro):
    return asyncio.run(coro)


def _capability(name: str, *, tag: str, tool: str = "probe") -> Capability:
    return Capability(
        id=name,
        description=f"{name} capability",
        safety="read-only",
        tools=(CapabilityTool(name=tool, argv=(tool,)),),
        tags=(tag,),
    )


def test_logs_query_reads_all_retained_rotation_segments(tmp_path: Path, monkeypatch):
    current = tmp_path / "agent.log"
    previous = tmp_path / "agent.log.1"
    previous.write_text(
        json.dumps({"tool_name": "older", "execution_outcome": "failed"}) + "\n",
        encoding="utf-8",
    )
    current.write_text(
        json.dumps({"tool_name": "newer", "execution_outcome": "succeeded"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "LOG_FILE", current)

    result = run(server.handle_logs_query({"filter": "all", "last_n": 20}))

    assert result["scope"] == "persistent_history"
    assert result["matched"] == 2
    assert [event["tool_name"] for event in result["events"]] == ["older", "newer"]


def test_workflow_plan_preserves_tied_top_candidates(tmp_path: Path, monkeypatch):
    capabilities = {
        "alpha": _capability("alpha", tag="shared"),
        "beta": _capability("beta", tag="shared"),
    }
    monkeypatch.setattr(server, "load_capabilities", lambda: capabilities)
    monkeypatch.setattr(server, "select_available_tool", lambda capability: capability.tools[0])

    result = run(server.handle_workflow_plan({"task": "shared", "cwd": str(tmp_path)}))

    assert result["selection_status"] == "ambiguous"
    assert result["selected_candidate"] is None
    assert result["matched_candidate"] is None
    assert [item["capability"] for item in result["candidate_capabilities"][:2]] == ["alpha", "beta"]
    assert "tied" in result["phases"][1]["action"].lower()
    assert result["phases"][2]["safety_class"] == "unresolved"


def test_workflow_plan_does_not_select_unique_match_without_executable(tmp_path: Path, monkeypatch):
    capabilities = {"alpha": _capability("alpha", tag="unique")}
    monkeypatch.setattr(server, "load_capabilities", lambda: capabilities)
    monkeypatch.setattr(server, "select_available_tool", lambda _capability: None)

    result = run(server.handle_workflow_plan({"task": "unique", "cwd": str(tmp_path)}))

    assert result["selection_status"] == "matched_unavailable"
    assert result["selected_candidate"] is None
    assert result["matched_candidate"]["capability"] == "alpha"
    assert result["matched_candidate"]["available_tool"] is None
    assert "no configured tools are available" in result["phases"][1]["action"].lower()


def test_payload_walk_rejects_reparse_boundary_before_descent(tmp_path: Path, monkeypatch):
    root = tmp_path / "bundle"
    boundary = root / "junction"
    boundary.mkdir(parents=True)
    (boundary / "inside.txt").write_text("should not be staged", encoding="utf-8")
    monkeypatch.setattr(server, "_is_reparse_point", lambda path: path == boundary)

    sources, error = server._payload_sources(tmp_path, ["bundle"])

    assert sources is None
    assert "reparse point" in str(error)


@pytest.mark.skipif(os.name != "nt", reason="NTFS junction contract is Windows-specific")
def test_payload_rejects_real_windows_junction(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bundle = workspace / "bundle"
    bundle.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    junction = bundle / "junction"

    created = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(outside)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable on runner: {created.stderr or created.stdout}")

    assert server._is_reparse_point(junction) is True
    sources, error = server._payload_sources(workspace, ["bundle"])
    assert sources is None
    assert "reparse point" in str(error)
