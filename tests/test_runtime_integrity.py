"""Regression tests for runtime authority and boundary integrity."""

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


def _capability(name: str, *, tag: str, tool: str = "probe", description: str | None = None) -> Capability:
    return Capability(
        id=name,
        description=description or f"{name} capability",
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


def test_logs_query_survives_retained_segment_disappearing_after_enumeration(tmp_path: Path, monkeypatch):
    current = tmp_path / "agent.log"
    vanished = tmp_path / "agent.log.1"
    current.write_text(
        json.dumps({"tool_name": "current", "execution_outcome": "succeeded"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "LOG_FILE", current)
    monkeypatch.setattr(server, "history_log_files", lambda _log: [vanished, current])

    result = run(server.handle_logs_query({"filter": "all", "last_n": 20}))

    assert result["matched"] == 1
    assert result["events"][0]["tool_name"] == "current"


def test_task_tokens_preserve_plural_and_singular_forms():
    tokens = server._task_tokens("Run the Python tests")
    assert {"python", "tests", "test"} <= tokens


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
    assert result["route_discriminator"]["candidates"] == ["alpha", "beta"]
    assert "tied" in result["phases"][1]["action"].lower()
    assert result["phases"][2]["safety_class"] == "unresolved"


def test_workflow_plan_does_not_use_backend_availability_to_break_semantic_tie(tmp_path: Path, monkeypatch):
    capabilities = {
        "alpha": _capability("alpha", tag="shared", tool="alpha-tool"),
        "beta": _capability("beta", tag="shared", tool="beta-tool"),
    }
    monkeypatch.setattr(server, "load_capabilities", lambda: capabilities)
    monkeypatch.setattr(
        server,
        "select_available_tool",
        lambda capability: capability.tools[0] if capability.id == "beta" else None,
    )

    result = run(server.handle_workflow_plan({"task": "shared", "cwd": str(tmp_path)}))

    assert result["selection_status"] == "ambiguous"
    assert result["selected_candidate"] is None
    assert result["route_discriminator"]["availability"] == {"alpha": None, "beta": "beta-tool"}


def test_workflow_plan_does_not_select_unique_match_without_executable(tmp_path: Path, monkeypatch):
    capabilities = {"alpha": _capability("alpha", tag="unique")}
    monkeypatch.setattr(server, "load_capabilities", lambda: capabilities)
    monkeypatch.setattr(server, "select_available_tool", lambda _capability: None)

    result = run(server.handle_workflow_plan({"task": "unique", "cwd": str(tmp_path)}))

    assert result["selection_status"] == "matched_unavailable"
    assert result["selected_candidate"] is None
    assert result["matched_candidate"]["capability"] == "alpha"
    assert result["matched_candidate"]["available_tool"] is None
    assert "configured tools are available" in result["phases"][1]["action"].lower()


def test_workflow_plan_does_not_select_from_description_only_similarity(tmp_path: Path, monkeypatch):
    capabilities = {
        "alpha": _capability(
            "alpha",
            tag="specialized",
            description="Inspect deployment manifest and summarize deployment state",
        )
    }
    monkeypatch.setattr(server, "load_capabilities", lambda: capabilities)
    monkeypatch.setattr(server, "select_available_tool", lambda capability: capability.tools[0])

    result = run(server.handle_workflow_plan({"task": "inspect the deployment manifest", "cwd": str(tmp_path)}))

    assert result["selection_status"] == "no_match"
    assert result["selected_candidate"] is None
    assert result["candidate_capabilities"][0]["description_score"] > 0
    assert result["candidate_capabilities"][0]["discriminating_score"] == 0
    assert "description-only" in result["route_discriminator"]["reason"]


def test_workflow_plan_does_not_select_from_generic_identity_words_alone(tmp_path: Path, monkeypatch):
    capabilities = {
        "test-alpha": _capability("test-alpha", tag="test"),
    }
    monkeypatch.setattr(server, "load_capabilities", lambda: capabilities)
    monkeypatch.setattr(server, "select_available_tool", lambda capability: capability.tools[0])

    result = run(server.handle_workflow_plan({"task": "run tests", "cwd": str(tmp_path)}))

    assert result["selection_status"] == "no_match"
    assert result["candidate_capabilities"][0]["identity_score"] > 0
    assert result["candidate_capabilities"][0]["discriminating_score"] == 0


def test_workflow_plan_can_select_from_discriminating_identity_evidence(tmp_path: Path, monkeypatch):
    capabilities = {
        "test-python": _capability("test-python", tag="python", tool="pytest"),
        "test-dotnet": _capability("test-dotnet", tag="dotnet", tool="dotnet"),
    }
    monkeypatch.setattr(server, "load_capabilities", lambda: capabilities)
    monkeypatch.setattr(server, "select_available_tool", lambda capability: capability.tools[0])

    result = run(server.handle_workflow_plan({"task": "run the Python tests", "cwd": str(tmp_path)}))

    assert result["selection_status"] == "selected"
    assert result["selected_candidate"]["capability"] == "test-python"
    assert "python" in result["selected_candidate"]["discriminating_matches"]


def test_workflow_plan_returns_all_candidates_not_only_first_three(tmp_path: Path, monkeypatch):
    capabilities = {
        name: _capability(name, tag=name)
        for name in ("alpha", "beta", "gamma", "delta")
    }
    monkeypatch.setattr(server, "load_capabilities", lambda: capabilities)
    monkeypatch.setattr(server, "select_available_tool", lambda capability: capability.tools[0])

    result = run(server.handle_workflow_plan({"task": "alpha", "cwd": str(tmp_path)}))

    assert len(result["candidate_capabilities"]) == 4


def test_payload_walk_rejects_reparse_boundary_before_descent(tmp_path: Path, monkeypatch):
    root = tmp_path / "bundle"
    boundary = root / "junction"
    boundary.mkdir(parents=True)
    (boundary / "inside.txt").write_text("should not be staged", encoding="utf-8")
    monkeypatch.setattr(server, "_is_reparse_point", lambda path: path == boundary)

    sources, error = server._payload_sources(tmp_path, ["bundle"])

    assert sources is None
    assert "reparse point" in str(error)


def test_payload_rejects_reparse_parent_before_selected_child_is_resolved(tmp_path: Path, monkeypatch):
    boundary = tmp_path / "junction"
    boundary.mkdir()
    (boundary / "inside.txt").write_text("inside", encoding="utf-8")
    original = server._is_reparse_point
    monkeypatch.setattr(
        server,
        "_is_reparse_point",
        lambda path: True if path == boundary else original(path),
    )

    sources, error = server._payload_sources(tmp_path, ["junction/inside.txt"])

    assert sources is None
    assert "crosses a symbolic link or reparse point" in str(error)


def test_payload_fails_closed_when_reparse_metadata_cannot_be_read(tmp_path: Path, monkeypatch):
    payload = tmp_path / "payload.txt"
    payload.write_text("x", encoding="utf-8")
    original_stat = server.os.stat

    def fail_selected(path, *args, **kwargs):
        if Path(path) == payload and kwargs.get("follow_symlinks") is False:
            raise PermissionError("metadata denied")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(server.os, "stat", fail_selected)
    sources, error = server._payload_sources(tmp_path, ["payload.txt"])

    assert sources is None
    assert "metadata could not be established" in str(error)


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
