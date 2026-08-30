"""Host startup performs best-effort stale Windows Sandbox cleanup before MCP transport."""

from pathlib import Path

from src import claude_server, codex_server
from src.mcp import server as common
from src.runtime_paths import resolve_codex_data_dir


def test_claude_startup_janitors_before_stdio(monkeypatch):
    events = []
    monkeypatch.setattr(common, "_cleanup_stale_sandbox_bundles", lambda: events.append("cleanup"))
    monkeypatch.setattr(claude_server, "run_stdio", lambda *_args, **_kwargs: events.append("stdio") or 0)

    assert claude_server.main_sync() == 0
    assert events == ["cleanup", "stdio"]


def test_codex_startup_janitors_bound_persistent_data_before_stdio(monkeypatch, tmp_path: Path):
    events = []
    monkeypatch.delenv("WINDOWS_DEV_AGENT_DATA_DIR", raising=False)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    def cleanup():
        events.append(("cleanup", common.DATA_DIR))

    monkeypatch.setattr(common, "_cleanup_stale_sandbox_bundles", cleanup)
    monkeypatch.setattr(codex_server, "run_stdio", lambda *_args, **_kwargs: events.append(("stdio", None)) or 0)

    assert codex_server.main_sync() == 0
    assert events[0] == ("cleanup", resolve_codex_data_dir())
    assert events[1] == ("stdio", None)
