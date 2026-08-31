"""Narrow Windows-native metadata queries used by WDA discovery surfaces."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Optional

from src.file_guard import guarded_open_read


VS_FFI_SIGNATURE = 0xFEEF04BD
FILE_VER_GET_NEUTRAL = 0x02


class _VSFixedFileInfo(ctypes.Structure):
    _fields_ = [
        ("dwSignature", ctypes.c_uint32),
        ("dwStrucVersion", ctypes.c_uint32),
        ("dwFileVersionMS", ctypes.c_uint32),
        ("dwFileVersionLS", ctypes.c_uint32),
        ("dwProductVersionMS", ctypes.c_uint32),
        ("dwProductVersionLS", ctypes.c_uint32),
        ("dwFileFlagsMask", ctypes.c_uint32),
        ("dwFileFlags", ctypes.c_uint32),
        ("dwFileOS", ctypes.c_uint32),
        ("dwFileType", ctypes.c_uint32),
        ("dwFileSubtype", ctypes.c_uint32),
        ("dwFileDateMS", ctypes.c_uint32),
        ("dwFileDateLS", ctypes.c_uint32),
    ]


def _version_parts(ms: int, ls: int) -> tuple[int, int, int, int]:
    return (
        (ms >> 16) & 0xFFFF,
        ms & 0xFFFF,
        (ls >> 16) & 0xFFFF,
        ls & 0xFFFF,
    )


def _system_version_library():
    """Load version.dll from the Windows system directory, never an ambient DLL path."""
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_system_directory = kernel32.GetSystemDirectoryW
    get_system_directory.argtypes = [wintypes.LPWSTR, wintypes.UINT]
    get_system_directory.restype = wintypes.UINT

    buffer = ctypes.create_unicode_buffer(32_768)
    length = int(get_system_directory(buffer, len(buffer)))
    if length <= 0 or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error() or 1)
    return ctypes.WinDLL(str(Path(buffer.value) / "version.dll"), use_last_error=True)


def _fixed_version_from_open_file(stream) -> Optional[str]:
    """Read fixed version metadata from the exact already-open Windows file handle."""
    import msvcrt
    from ctypes import wintypes

    version = _system_version_library()
    try:
        get_by_handle = version.GetFileVersionInfoByHandle
    except AttributeError:
        return None
    get_by_handle.argtypes = [
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_by_handle.restype = wintypes.BOOL
    query = version.VerQueryValueW
    query.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    query.restype = wintypes.BOOL

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    local_free = kernel32.LocalFree
    local_free.argtypes = [ctypes.c_void_p]
    local_free.restype = ctypes.c_void_p

    data = ctypes.c_void_p()
    data_length = wintypes.DWORD()
    handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
    try:
        if not get_by_handle(
            FILE_VER_GET_NEUTRAL,
            handle,
            ctypes.byref(data),
            ctypes.byref(data_length),
        ):
            return None
        if not data.value or data_length.value == 0:
            return None

        value = ctypes.c_void_p()
        length = wintypes.UINT()
        if not query(data, "\\", ctypes.byref(value), ctypes.byref(length)):
            return None
        if not value.value or length.value < ctypes.sizeof(_VSFixedFileInfo):
            return None
        info = ctypes.cast(value, ctypes.POINTER(_VSFixedFileInfo)).contents
        if info.dwSignature != VS_FFI_SIGNATURE:
            return None
        parts = _version_parts(info.dwFileVersionMS, info.dwFileVersionLS)
        if not any(parts):
            return None
        return ".".join(str(part) for part in parts)
    finally:
        if data.value:
            local_free(data)


def windows_file_version(path: Path | str) -> Optional[str]:
    """Return fixed Windows file-version metadata without launching the target.

    WDA opens and validates the physical file once, denies competing write/delete
    opens, then asks Windows for version information from that exact open handle.
    There is no second path lookup between identity establishment and metadata
    consumption. Files without a version resource return ``None``.
    """
    if os.name != "nt":
        return None
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        return None

    try:
        with guarded_open_read(requested, exact_path=True, deny_write_delete=True) as stream:
            return _fixed_version_from_open_file(stream)
    except OSError:
        return None
