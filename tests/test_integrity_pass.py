"""Regression contracts for routing discrimination, retained history, and Sandbox path boundaries."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import stat
import subprocess
from types import SimpleNamespace

import pytest

from src.capabilities import Capability, CapabilityTool
from src.mcp import server


def run(coro):
    return asyncio.run(coro)


def _capability(cap_id: str, description: str, tags: tuple[str, ...], tool_name: str) -> Capability:
    return Capability(
        id=cap_id,
        description=description,
        safety="reversible",
        tools=(CapabilityTool(name=tool_name, argv=(tool_name, "--version")),),
        tags=tags,
    )


def test_workflow_plan_preserves_equal_top_route_ambiguity(tmp_path: Path, monkeypatch):
    capabilities = {
        "alpha": _capability("alpha", "Handle shared route", ("shared", "route"), "alpha-tool"),
        "beta": _capability("beta", "Handle shared route", ("shared", "route"), "beta-tool"),
    }
    monkeypatch.setattr(server, "load_capabilities", lambda: capabilities)
    monkeypatch.setattr(server, "select_available_tool", lambda capability: capability.tools[0])

    result = run(server.handle_workflow_plan({"task": "shared route", "cwd": str(tmp_path)}))

    assert result["route_state"] == "ambiguous"
    assert result["selected_candidate"] is None
    assert result["route_discriminator"]["candidates"] == ["alpha", "beta"]
    assert result["phases"][2]["action"] == "Do not execute from this scaffold yet"


def test_workflow_plan_does_not_fall_through_unavailable_strongest_match(tmp_path: Path, monkeypatch):
    capabilities = {
        "special": _capability("special", "Handle special unique task", ("special", "unique"), "special-tool"),
        "fallback": _capability("fallback", "Handle task", ("task",), "fallback-tool"),
    }
    monkeypatch.setattr(server, "load_capabilities", lambda: capabilities)
    monkeypatch.setattr(
        server,
        "select_available_tool",
        lambda capability: None if capability.id == "special" else capability.tools[0],
    )

    result = run(server.handle_workflow_plan({"task": "special unique task", "cwd": str(tmp_path)}))

    assert result["route_state"] == "unavailable"
    assert result["selected_candidate"] is None
    assert result["route_discriminator"]["candidates"] == ["special"]
    assert "fallback" not in result["route_discriminator"]["candidates"]


def test_workflow_plan_can_use_availability_to_break_equal_top_tie(tmp_path: Path, monkeypatch):
    capabilities = {
        "alpha": _capability("alpha", "Handle shared route", ("shared", "route"), "alpha-tool"),
        "beta": _capability("beta", "Handle shared route", ("shared", "route"), "beta-tool"),
    }
    monkeypatch.setattr(server, "load_capabilities", lambda: capabilities)
    monkeypatch.setattr(
        server,
        "select_available_tool",
        lambda capability: capability.tools[0] if capability.id == "beta" else None,
    )

    result = run(server.handle_workflow_plan({"task": "shared route", "cwd": str(tmp_path)}))

    assert result["route_state"] == "selected"
    assert result["selected_candidate"]["capability"] == "beta"


def test_logs_query_reads_rotated_predecessor_before_current_log(tmp_path: Path, monkeypatch):
    log = tmp_path / "agent.log"
    previous = tmp_path / "agent.log.1"
    previous.write_text(json.dumps({"tool_name": "older", "execution_outcome": "failed"}) + "\n", encoding="utf-8")
    log.write_text(json.dumps({"tool_name": "newer", "execution_outcome": "succeeded"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(server, "LOG_FILE", log)

    result = run(server.handle_logs_query({"filter": "all", "last_n": 10}))

    assert result["scope"] == "bounded_persistent_history"
    assert result["matched"] == 2
    assert [event["tool_name"] for event in result["events"]] == ["older", "newer"]


def test_reparse_attribute_is_treated_as_boundary_without_platform_dependency():
    fake = SimpleNamespace(
        lstat=lambda: SimpleNamespace(
            st_mode=stat.S_IFDIR,
            st_file_attributes=server._REPARSE_POINT_ATTRIBUTE,
        )
    )
    assert server._is_reparse_point(fake) is True


def test_payload_tree_rejects_nested_reparse_before_descending(tmp_path: Path, monkeypatch):
    bundle = tmp_path / "bundle"
    nested = bundle / "junction"
    nested.mkdir(parents=True)
    (nested / "inside.txt").write_text("x", encoding="utf-8")

    original = server._is_reparse_point

    def fake_reparse(path):
        if getattr(path, "name", "") == "junction":
            return True
        return original(path)

    monkeypatch.setattr(server, "_is_reparse_point", fake_reparse)
    sources, error = server._payload_sources(tmp_path, ["bundle"])

    assert sources is None
    assert "reparse point" in error
    assert "junction" in error


def test_payload_path_rejects_reparse_parent_even_when_target_stays_in_workspace(tmp_path: Path, monkeypatch):
    junction = tmp_path / "junction"
    junction.mkdir()
    target = junction / "inside.txt"
    target.write_text("x", encoding="utf-8")

    original = server._is_reparse_point

    def fake_reparse(path):
        if getattr(path, "name", "") == "junction":
            return True
        return original(path)

    monkeypatch.setattr(server, "_is_reparse_point", fake_reparse)
    sources, error = server._payload_sources(tmp_path, ["junction/inside.txt"])

    assert sources is None
    assert "crosses a symbolic link or reparse point" in error


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
