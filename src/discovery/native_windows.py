"""Focused Windows-owned probes that are not exposed cleanly by the broad CIM snapshot."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Any, Optional

from src.execution import resolve_windows_system_executable, run_bounded


FSCTL_QUERY_PERSISTENT_VOLUME_STATE = 590396
PERSISTENT_VOLUME_STATE_DEV_VOLUME = 0x00002000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
DRIVE_FIXED = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class _PersistentVolumeInformation(ctypes.Structure):
    _fields_ = [
        ("VolumeFlags", ctypes.c_uint32),
        ("FlagMask", ctypes.c_uint32),
        ("Version", ctypes.c_uint32),
        ("Reserved", ctypes.c_uint32),
    ]


def _developer_drive_enablement() -> tuple[Optional[bool], str, Optional[str]]:
    """Query the Windows developer-drive feature state through its Win32 API."""
    if os.name != "nt":
        return None, "unknown", "Developer Drive state is only available on Windows"
    try:
        api = ctypes.WinDLL("api-ms-win-core-sysinfo-l1-2-6.dll", use_last_error=True)
        query = api.GetDeveloperDriveEnablementState
        query.argtypes = []
        query.restype = ctypes.c_int
        state = int(query())
    except (AttributeError, OSError) as exc:
        return None, "unknown", f"Developer Drive enablement API unavailable: {exc}"

    if state == 1:
        return True, "enabled", None
    if state == 2:
        return False, "disabled_by_system_policy", None
    if state == 3:
        return False, "disabled_by_group_policy", None
    error = ctypes.get_last_error()
    suffix = f" (Win32 error {error})" if error else ""
    return None, "unknown", f"Developer Drive enablement was not established{suffix}"


def _fixed_drive_roots() -> list[str]:
    if os.name != "nt":
        return []
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_drives = kernel32.GetLogicalDrives
    get_drives.argtypes = []
    get_drives.restype = ctypes.c_uint32
    get_type = kernel32.GetDriveTypeW
    get_type.argtypes = [ctypes.c_wchar_p]
    get_type.restype = ctypes.c_uint32

    mask = int(get_drives())
    if mask == 0 and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    roots: list[str] = []
    for index in range(26):
        if not (mask & (1 << index)):
            continue
        root = f"{chr(ord('A') + index)}:\\"
        if int(get_type(root)) == DRIVE_FIXED:
            roots.append(root)
    return roots


def _is_developer_volume(root: str) -> bool:
    """Read the persistent DEV_VOLUME flag from the mounted volume itself."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    device_io = kernel32.DeviceIoControl
    device_io.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
    ]
    device_io.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    volume_name = rf"\\.\{root[:2]}"
    # This is a query-only FSCTL. Request no data access and share the volume with
    # ordinary readers/writers; Windows still decides whether the control query is permitted.
    handle = create_file(
        volume_name,
        0,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if handle in (None, INVALID_HANDLE_VALUE):
        raise ctypes.WinError(ctypes.get_last_error() or 1)
    try:
        incoming = _PersistentVolumeInformation()
        incoming.FlagMask = PERSISTENT_VOLUME_STATE_DEV_VOLUME
        incoming.Version = 1
        outgoing = _PersistentVolumeInformation()
        returned = wintypes.DWORD()
        ok = device_io(
            handle,
            FSCTL_QUERY_PERSISTENT_VOLUME_STATE,
            ctypes.byref(incoming),
            ctypes.sizeof(incoming),
            ctypes.byref(outgoing),
            ctypes.sizeof(outgoing),
            ctypes.byref(returned),
            None,
        )
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error() or 1)
        return bool(outgoing.VolumeFlags & PERSISTENT_VOLUME_STATE_DEV_VOLUME)
    finally:
        close_handle(handle)


def _volume_metadata(root: str) -> dict[str, Any]:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_info = kernel32.GetVolumeInformationW
    get_info.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.LPWSTR,
        wintypes.DWORD,
    ]
    get_info.restype = wintypes.BOOL
    get_space = kernel32.GetDiskFreeSpaceExW
    get_space.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
    ]
    get_space.restype = wintypes.BOOL

    label = ctypes.create_unicode_buffer(261)
    serial = wintypes.DWORD()
    max_component = wintypes.DWORD()
    flags = wintypes.DWORD()
    filesystem = ctypes.create_unicode_buffer(261)
    if not get_info(root, label, len(label), ctypes.byref(serial), ctypes.byref(max_component), ctypes.byref(flags), filesystem, len(filesystem)):
        raise ctypes.WinError(ctypes.get_last_error() or 1)

    free_available = ctypes.c_ulonglong()
    total = ctypes.c_ulonglong()
    total_free = ctypes.c_ulonglong()
    if not get_space(root, ctypes.byref(free_available), ctypes.byref(total), ctypes.byref(total_free)):
        raise ctypes.WinError(ctypes.get_last_error() or 1)

    return {
        "drive_letter": root[0],
        "label": label.value,
        "size_gb": round(total.value / (1024 ** 3), 2),
        "free_space_gb": round(total_free.value / (1024 ** 3), 2),
    }


def _developer_drive_inventory() -> tuple[Optional[list[dict[str, Any]]], list[str]]:
    """Enumerate mounted fixed Dev Drives using the volume's native persistent flag."""
    if os.name != "nt":
        return None, ["Developer Drive inventory is only available on Windows"]
    drives: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        roots = _fixed_drive_roots()
    except OSError as exc:
        return None, [f"Fixed-volume enumeration failed: {exc}"]

    for root in roots:
        try:
            if _is_developer_volume(root):
                drives.append(_volume_metadata(root))
        except OSError as exc:
            errors.append(f"Developer Drive identity was not established for {root}: {exc}")
    if errors:
        return None, errors
    return drives, []


def probe_native_virtualization() -> dict[str, Any]:
    """Return WSL and Dev Drive state from Windows-owned control surfaces."""
    errors: list[str] = []
    execution_started = False

    wsl_installed: Optional[bool]
    wsl_version: Optional[str] = None
    if os.name != "nt":
        wsl_installed = None
        errors.append("WSL state is only available on Windows")
    else:
        wsl = resolve_windows_system_executable("wsl.exe")
        if wsl is None:
            wsl_installed = False
        else:
            status = run_bounded([wsl, "--status"], timeout=10, stdout_bytes=32_768, stderr_bytes=32_768)
            execution_started = status.get("execution_started") is True
            if status.get("execution_started") is False:
                wsl_installed = None
                errors.append(f"WSL status probe did not start: {status.get('error', 'unknown launch error')}")
            elif status.get("timed_out") is True:
                wsl_installed = None
                errors.append("WSL status probe timed out")
            elif status.get("returncode") == 0:
                wsl_installed = True
            else:
                wsl_installed = None
                errors.append(f"WSL status probe exited with code {status.get('returncode')}")

    dev_drive_enabled, dev_drive_state, enablement_error = _developer_drive_enablement()
    if enablement_error:
        errors.append(enablement_error)
    dev_drives, inventory_errors = _developer_drive_inventory()
    errors.extend(inventory_errors)

    return {
        "wsl_installed": wsl_installed,
        "wsl_version": wsl_version,
        "dev_drive_enabled": dev_drive_enabled,
        "dev_drive_state": dev_drive_state,
        "dev_drives": dev_drives,
        "execution_started": execution_started,
        "errors": errors,
    }
