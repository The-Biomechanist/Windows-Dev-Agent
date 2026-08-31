"""Native Windows authority tests that do not launch WSL during planning."""

from __future__ import annotations

from pathlib import Path
import sys

from src import windows_state


def _executable() -> str:
    return str(Path(sys.executable).resolve())


def test_wsl_route_requires_registered_default_distribution(monkeypatch):
    monkeypatch.setattr(windows_state, "_read_policy_dword", lambda _name: (None, None))
    monkeypatch.setattr(
        windows_state,
        "_registered_default_distribution",
        lambda: (None, None, True, None),
    )
    state = windows_state.query_wsl_route_state(_executable())
    assert state.available is False
    assert "default WSL distribution" in (state.reason or "")


def test_wsl_route_accepts_store_or_inbox_agnostic_registered_default(monkeypatch):
    monkeypatch.setattr(windows_state, "_read_policy_dword", lambda _name: (None, None))
    monkeypatch.setattr(
        windows_state,
        "_registered_default_distribution",
        lambda: ("Ubuntu", 2, True, None),
    )
    state = windows_state.query_wsl_route_state(_executable())
    assert state.available is True
    assert state.default_distribution == "Ubuntu"


def test_wsl_route_respects_global_policy(monkeypatch):
    monkeypatch.setattr(
        windows_state,
        "_read_policy_dword",
        lambda name: (0, None) if name == "AllowWSL" else (None, None),
    )
    monkeypatch.setattr(
        windows_state,
        "_registered_default_distribution",
        lambda: ("Ubuntu", 2, True, None),
    )
    state = windows_state.query_wsl_route_state(_executable())
    assert state.available is False
    assert "policy disables WSL" in (state.reason or "")


def test_wsl1_route_respects_wsl1_policy(monkeypatch):
    def policy(name: str):
        if name == "AllowWSL1":
            return 0, None
        return None, None

    monkeypatch.setattr(windows_state, "_read_policy_dword", policy)
    monkeypatch.setattr(
        windows_state,
        "_registered_default_distribution",
        lambda: ("Legacy", 1, True, None),
    )
    state = windows_state.query_wsl_route_state(_executable())
    assert state.available is False
    assert state.default_distribution == "Legacy"
    assert "WSL 1" in (state.reason or "")


def test_wsl_route_preserves_unknown_registry_state(monkeypatch):
    monkeypatch.setattr(
        windows_state,
        "_read_policy_dword",
        lambda _name: (None, "policy read failed"),
    )
    state = windows_state.query_wsl_route_state(_executable())
    assert state.available is None
    assert state.reason == "policy read failed"


def test_wsl_route_requires_system_executable():
    state = windows_state.query_wsl_route_state(None)
    assert state.available is False
    assert "executable" in (state.reason or "")
