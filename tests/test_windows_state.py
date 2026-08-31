"""Native Windows authority tests that do not launch WSL during planning."""

from __future__ import annotations

from pathlib import Path
import sys

from src import windows_state


def _executable() -> str:
    return str(Path(sys.executable).resolve())


def _mock_services(monkeypatch, *, store: bool = True, inbox: bool = False) -> None:
    def registered(key_path: str):
        if key_path == windows_state.WSL_STORE_SERVICE_KEY:
            return store, None
        if key_path == windows_state.WSL_INBOX_SERVICE_KEY:
            return inbox, None
        raise AssertionError(key_path)

    monkeypatch.setattr(windows_state, "_service_registered", registered)


def test_wsl_route_requires_registered_default_distribution(monkeypatch):
    _mock_services(monkeypatch)
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
    _mock_services(monkeypatch)
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
    _mock_services(monkeypatch)
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


def test_inbox_wsl_route_respects_inbox_policy(monkeypatch):
    _mock_services(monkeypatch, store=False, inbox=True)

    def policy(name: str):
        if name == "AllowInboxWSL":
            return 0, None
        return None, None

    monkeypatch.setattr(windows_state, "_read_policy_dword", policy)
    monkeypatch.setattr(
        windows_state,
        "_registered_default_distribution",
        lambda: ("Ubuntu", 2, True, None),
    )
    state = windows_state.query_wsl_route_state(_executable())
    assert state.available is False
    assert "Inbox WSL" in (state.reason or "")


def test_store_wsl_route_is_not_blocked_by_inbox_policy(monkeypatch):
    _mock_services(monkeypatch, store=True, inbox=True)

    def policy(name: str):
        if name == "AllowInboxWSL":
            return 0, None
        return None, None

    monkeypatch.setattr(windows_state, "_read_policy_dword", policy)
    monkeypatch.setattr(
        windows_state,
        "_registered_default_distribution",
        lambda: ("Ubuntu", 2, True, None),
    )
    state = windows_state.query_wsl_route_state(_executable())
    assert state.available is True


def test_unknown_default_distro_version_is_unknown_when_wsl1_is_disabled(monkeypatch):
    _mock_services(monkeypatch)

    def policy(name: str):
        if name == "AllowWSL1":
            return 0, None
        return None, None

    monkeypatch.setattr(windows_state, "_read_policy_dword", policy)
    monkeypatch.setattr(
        windows_state,
        "_registered_default_distribution",
        lambda: ("Ubuntu", None, True, None),
    )
    state = windows_state.query_wsl_route_state(_executable())
    assert state.available is None
    assert "version is unknown" in (state.reason or "")
