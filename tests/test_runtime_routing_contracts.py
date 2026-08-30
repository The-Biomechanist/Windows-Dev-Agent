"""Focused runtime contracts for reachable routing and noninteractive host execution."""

import asyncio
import json
from pathlib import Path

from src.capabilities import Capability, CapabilityTool
from src.mcp import server

ROOT = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


def test_workflow_plan_never_selects_an_unavailable_capability(tmp_path: Path, monkeypatch):
    unavailable = Capability(
        id="python-test-primary",
        description="Run Python tests with the preferred test runner",
        safety="reversible",
        tools=(CapabilityTool(name="missing-runner", argv=("missing-runner",)),),
        tags=("python", "test"),
    )
    reachable = Capability(
        id="python-test-fallback",
        description="Run Python tests",
        safety="reversible",
        tools=(CapabilityTool(name="pytest", argv=("pytest",)),),
        tags=("python", "test"),
    )
    monkeypatch.setattr(
        server,
        "load_capabilities",
        lambda: {unavailable.id: unavailable, reachable.id: reachable},
    )
    monkeypatch.setattr(
        server,
        "select_available_tool",
        lambda capability: None if capability.id == unavailable.id else reachable.tools[0],
    )

    result = run(
        server.handle_workflow_plan(
            {"task": "run Python tests with the preferred test runner", "cwd": str(tmp_path)}
        )
    )

    assert result["candidate_capabilities"][0]["capability"] == unavailable.id
    assert result["candidate_capabilities"][0]["available_tool"] is None
    assert result["selected_candidate"]["capability"] == reachable.id
    assert result["selected_candidate"]["available_tool"] == "pytest"


def test_workflow_plan_has_no_selected_route_when_all_matches_are_unavailable(tmp_path: Path, monkeypatch):
    capability = Capability(
        id="python-test",
        description="Run Python tests",
        safety="reversible",
        tools=(CapabilityTool(name="missing", argv=("missing",)),),
        tags=("python", "test"),
    )
    monkeypatch.setattr(server, "load_capabilities", lambda: {capability.id: capability})
    monkeypatch.setattr(server, "select_available_tool", lambda _capability: None)

    result = run(server.handle_workflow_plan({"task": "run Python tests", "cwd": str(tmp_path)}))

    assert result["selected_candidate"] is None
    assert "Select capability" not in result["phases"][1]["action"]


def test_winget_install_plan_disables_hidden_interaction():
    plan = run(
        server.handle_package_install(
            {"package_id": "Python.Python.3.12", "source": "winget", "execute": False}
        )
    )

    assert plan["status"] == "planned"
    assert "--disable-interactivity" in plan["argv"]
    assert "--accept-package-agreements" in plan["argv"]
    assert "--accept-source-agreements" in plan["argv"]


def test_codex_mcp_timeout_exceeds_runtime_execution_ceiling():
    config = json.loads((ROOT / ".mcp.codex.json").read_text(encoding="utf-8"))
    mcp = config["windows_dev_agent"]

    assert mcp["tool_timeout_sec"] > 600
