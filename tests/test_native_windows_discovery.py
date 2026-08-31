"""Contracts for Windows-owned WSL and Dev Drive discovery surfaces."""

from __future__ import annotations

import os

import pytest

from src.discovery import native_windows


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows-native discovery")


def test_wsl_availability_comes_from_windows_control_plane(monkeypatch):
    monkeypatch.setattr(native_windows, "resolve_windows_system_executable", lambda name: r"C:\Windows\System32\wsl.exe" if name == "wsl.exe" else None)
    monkeypatch.setattr(
        native_windows,
        "run_bounded",
        lambda argv, **_kwargs: {
            "argv": argv,
            "returncode": 0,
            "succeeded": True,
            "execution_started": True,
            "stdout": "",
            "stderr": "",
        },
    )
    monkeypatch.setattr(native_windows, "_developer_drive_enablement", lambda: (None, "unsupported_api", None))
    monkeypatch.setattr(native_windows, "_developer_drive_inventory", lambda: (None, []))

    result = native_windows.probe_native_virtualization()

    assert result["wsl_installed"] is True
    assert result["execution_started"] is True
    assert result["errors"] == []


def test_wsl_nonzero_status_is_observed_unavailable(monkeypatch):
    monkeypatch.setattr(native_windows, "resolve_windows_system_executable", lambda _name: r"C:\Windows\System32\wsl.exe")
    monkeypatch.setattr(
        native_windows,
        "run_bounded",
        lambda *_args, **_kwargs: {
            "returncode": 50,
            "succeeded": False,
            "execution_started": True,
            "stdout": "",
            "stderr": "",
        },
    )
    monkeypatch.setattr(native_windows, "_developer_drive_enablement", lambda: (None, "unsupported_api", None))
    monkeypatch.setattr(native_windows, "_developer_drive_inventory", lambda: (None, []))

    result = native_windows.probe_native_virtualization()

    assert result["wsl_installed"] is False
    assert result["errors"] == []


def test_dev_drive_inventory_uses_native_volume_flag_not_label(monkeypatch):
    monkeypatch.setattr(native_windows, "_fixed_drive_roots", lambda: ["D:\\", "E:\\"])
    monkeypatch.setattr(native_windows, "_is_developer_volume", lambda root: root.startswith("D:"))
    monkeypatch.setattr(
        native_windows,
        "_volume_metadata",
        lambda root: {
            "drive_letter": root[0],
            "label": "ordinary-label",
            "size_gb": 100.0,
            "free_space_gb": 75.0,
        },
    )

    drives, errors = native_windows._developer_drive_inventory()

    assert errors == []
    assert drives == [
        {
            "drive_letter": "D",
            "label": "ordinary-label",
            "size_gb": 100.0,
            "free_space_gb": 75.0,
        }
    ]


def test_incomplete_dev_drive_identity_does_not_become_complete_inventory(monkeypatch):
    monkeypatch.setattr(native_windows, "_fixed_drive_roots", lambda: ["C:\\", "D:\\"])

    def query(root: str) -> bool:
        if root.startswith("D:"):
            raise OSError("query denied")
        return False

    monkeypatch.setattr(native_windows, "_is_developer_volume", query)

    drives, errors = native_windows._developer_drive_inventory()

    assert drives is None
    assert len(errors) == 1
    assert "D:\\" in errors[0]


def test_live_native_dev_drive_probe_returns_typed_state():
    enabled, state, error = native_windows._developer_drive_enablement()
    assert enabled in {True, False, None}
    assert state in {
        "enabled",
        "disabled_by_system_policy",
        "disabled_by_group_policy",
        "unsupported_api",
        "unknown",
    }
    assert error is None or isinstance(error, str)

    drives, errors = native_windows._developer_drive_inventory()
    assert drives is None or isinstance(drives, list)
    assert isinstance(errors, list)
