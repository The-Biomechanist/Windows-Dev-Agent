"""Narrow Windows-owned state queries used by routing decisions."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Optional

try:  # Windows-only module; keep imports testable on non-Windows hosts.
    import winreg  # type: ignore
except ImportError:  # pragma: no cover - Windows CI exercises the real module.
    winreg = None  # type: ignore[assignment]


WSL_POLICY_KEY = r"SOFTWARE\Policies\WSL"
WSL_USER_KEY = r"Software\Microsoft\Windows\CurrentVersion\Lxss"


@dataclass(frozen=True)
class WslRouteState:
    """Static authority state for WDA's default-distribution WSL route."""

    available: Optional[bool]
    default_distribution: Optional[str] = None
    reason: Optional[str] = None


def _read_policy_dword(name: str) -> tuple[Optional[int], Optional[str]]:
    """Read a WSL machine policy; absence means the policy is not configured."""
    if winreg is None:
        return None, "Windows registry access is unavailable"
    access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, WSL_POLICY_KEY, 0, access) as key:
            try:
                value, _kind = winreg.QueryValueEx(key, name)
            except FileNotFoundError:
                return None, None
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"WSL policy state could not be established: {exc}"
    try:
        return int(value), None
    except (TypeError, ValueError):
        return None, f"WSL policy {name} has an invalid value"


def _registered_default_distribution() -> tuple[Optional[str], Optional[int], Optional[bool], Optional[str]]:
    """Return the registered default distro, its WSL version, and inventory state."""
    if winreg is None:
        return None, None, None, "Windows registry access is unavailable"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WSL_USER_KEY, 0, winreg.KEY_READ) as root:
            try:
                default_id, _kind = winreg.QueryValueEx(root, "DefaultDistribution")
            except FileNotFoundError:
                return None, None, True, None
            default_id = str(default_id or "").strip()
            if not default_id:
                return None, None, True, None
            try:
                with winreg.OpenKey(root, default_id, 0, winreg.KEY_READ) as distro:
                    try:
                        name, _kind = winreg.QueryValueEx(distro, "DistributionName")
                    except FileNotFoundError:
                        return None, None, True, None
                    try:
                        version, _kind = winreg.QueryValueEx(distro, "Version")
                        version_value: Optional[int] = int(version)
                    except (FileNotFoundError, TypeError, ValueError):
                        version_value = None
            except FileNotFoundError:
                return None, None, True, None
    except FileNotFoundError:
        return None, None, True, None
    except OSError as exc:
        return None, None, None, f"Registered WSL distribution state could not be established: {exc}"

    distribution_name = str(name or "").strip()
    if not distribution_name:
        return None, version_value, True, None
    return distribution_name, version_value, True, None


def query_wsl_route_state(wsl_executable: Optional[str]) -> WslRouteState:
    """Establish whether WDA can route a command to WSL's registered default distro.

    This is intentionally a static Windows authority query: it does not launch
    ``wsl.exe`` merely to plan execution.  Actual launch/runtime failure remains
    an execution outcome rather than being inferred here.
    """
    if not isinstance(wsl_executable, str) or not wsl_executable.strip():
        return WslRouteState(False, reason="Windows-owned WSL executable is not available")
    executable = Path(wsl_executable).expanduser()
    try:
        if not executable.is_absolute() or not executable.is_file():
            return WslRouteState(False, reason="Windows-owned WSL executable is not available")
    except OSError as exc:
        return WslRouteState(None, reason=f"WSL executable state could not be established: {exc}")

    allow_wsl, policy_error = _read_policy_dword("AllowWSL")
    if policy_error:
        return WslRouteState(None, reason=policy_error)
    if allow_wsl == 0:
        return WslRouteState(False, reason="Windows policy disables WSL")

    default_name, distro_version, inventory_established, distro_error = _registered_default_distribution()
    if distro_error:
        return WslRouteState(None, reason=distro_error)
    if inventory_established is not True:
        return WslRouteState(None, reason="Registered WSL distribution state could not be established")
    if not default_name:
        return WslRouteState(False, reason="No registered default WSL distribution is available")

    if distro_version == 1:
        allow_wsl1, policy_error = _read_policy_dword("AllowWSL1")
        if policy_error:
            return WslRouteState(None, default_distribution=default_name, reason=policy_error)
        if allow_wsl1 == 0:
            return WslRouteState(False, default_distribution=default_name, reason="Windows policy disables WSL 1")

    return WslRouteState(True, default_distribution=default_name)
