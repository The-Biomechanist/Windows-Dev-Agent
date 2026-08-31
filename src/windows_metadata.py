"""Narrow Windows-native metadata queries used by WDA discovery surfaces."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Optional

from src.file_guard import guarded_open_read


VS_FFI_SIGNATURE = 0xFEEF04BD


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


def windows_file_version(path: Path | str) -> Optional[str]:
    """Return the fixed Windows file version without launching the target.

    The physical file is held read-only with write/delete sharing denied while
    the Windows version-resource APIs query its path, preventing a same-path
    replacement from changing the object between admission and metadata read.
    Files without a version resource return ``None``.
    """
    if os.name != "nt":
        return None
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        return None

    from ctypes import wintypes

    version = ctypes.WinDLL("version", use_last_error=True)
    get_size = version.GetFileVersionInfoSizeW
    get_size.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    get_size.restype = wintypes.DWORD
    get_info = version.GetFileVersionInfoW
    get_info.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    get_info.restype = wintypes.BOOL
    query = version.VerQueryValueW
    query.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    query.restype = wintypes.BOOL

    try:
        with guarded_open_read(requested, exact_path=True, deny_write_delete=True):
            ignored = wintypes.DWORD()
            size = int(get_size(str(requested), ctypes.byref(ignored)))
            if size <= 0:
                return None
            buffer = ctypes.create_string_buffer(size)
            if not get_info(str(requested), 0, size, buffer):
                return None
            value = ctypes.c_void_p()
            length = wintypes.UINT()
            if not query(buffer, "\\", ctypes.byref(value), ctypes.byref(length)):
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
    except OSError:
        return None
