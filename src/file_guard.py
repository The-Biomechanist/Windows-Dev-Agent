"""Use-time file identity and project-bound read guards for Windows Dev Agent."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
from typing import BinaryIO, Iterator, Optional


EXECUTABLE_IDENTITY_FILE = "file"
EXECUTABLE_IDENTITY_APP_EXECUTION_ALIAS = "app_execution_alias"
EXECUTABLE_IDENTITY_KINDS = {
    EXECUTABLE_IDENTITY_FILE,
    EXECUTABLE_IDENTITY_APP_EXECUTION_ALIAS,
}
IO_REPARSE_TAG_APPEXECLINK = 0x8000001B
FSCTL_GET_REPARSE_POINT = 0x000900A8
MAXIMUM_REPARSE_DATA_BUFFER_SIZE = 16 * 1024
ERROR_NOT_A_REPARSE_POINT = 4390


class FileGuardError(OSError):
    """Base class for a path that cannot be safely consumed."""


class FileBoundaryError(FileGuardError):
    """Raised when an opened object resolves through an unexpected path boundary."""


class FileIdentityMismatch(FileGuardError):
    """Raised when current identity material does not match the reviewed fingerprint."""


@dataclass(frozen=True)
class ExecutableIdentity:
    """Stable identity material for one resolved executable object."""

    kind: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "sha256": self.sha256}


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in value)


def valid_executable_identity(kind: object, sha256: object) -> bool:
    return isinstance(kind, str) and kind in EXECUTABLE_IDENTITY_KINDS and valid_sha256(sha256)


def _hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    stream.seek(0)
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    stream.seek(0)
    return digest.hexdigest()


def _normalize_final_path(value: str) -> str:
    # GetFinalPathNameByHandleW commonly returns a Win32 device prefix.
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return os.path.normcase(os.path.abspath(value))


def _validate_final_path(
    requested: Path,
    final_norm: str,
    *,
    root: Optional[Path | str],
    exact_path: bool,
) -> None:
    requested_norm = os.path.normcase(os.path.abspath(str(requested)))
    if exact_path and final_norm != requested_norm:
        raise FileBoundaryError(f"opened path crossed a symbolic link or reparse boundary: {requested}")
    if root is None:
        return
    root_norm = os.path.normcase(str(Path(root).expanduser().resolve()))
    try:
        common = os.path.commonpath([root_norm, final_norm])
    except ValueError as exc:
        raise FileBoundaryError(f"opened path resolved outside project boundary: {requested}") from exc
    if os.path.normcase(common) != root_norm:
        raise FileBoundaryError(f"opened path resolved outside project boundary: {requested}")


def _final_path_from_handle(handle: int) -> str:
    if os.name != "nt":
        raise OSError("Windows handle path lookup is unavailable")
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    get_final_path.restype = wintypes.DWORD

    size = 32_768
    buffer = ctypes.create_unicode_buffer(size)
    length = get_final_path(wintypes.HANDLE(handle), buffer, size, 0)
    if not length or length >= size:
        error = ctypes.get_last_error()
        raise ctypes.WinError(error or 1)
    return _normalize_final_path(buffer.value)


def _opened_final_path(stream: BinaryIO) -> str:
    if os.name != "nt":
        return os.path.normcase(str(Path(stream.name).resolve()))
    import msvcrt

    return _final_path_from_handle(msvcrt.get_osfhandle(stream.fileno()))


def _create_windows_raw_handle(
    path: Path,
    *,
    desired_access: int,
    share_mode: int,
    flags: int,
) -> int:
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

    OPEN_EXISTING = 3
    invalid = ctypes.c_void_p(-1).value
    handle = create_file(str(path), desired_access, share_mode, None, OPEN_EXISTING, flags, None)
    if handle in (None, invalid):
        error = ctypes.get_last_error()
        raise ctypes.WinError(error or 1)
    return int(handle)


def _create_windows_handle(path: Path, *, directory: bool) -> int:
    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
    flags = FILE_ATTRIBUTE_NORMAL | (FILE_FLAG_BACKUP_SEMANTICS if directory else FILE_FLAG_SEQUENTIAL_SCAN)
    return _create_windows_raw_handle(
        path,
        desired_access=GENERIC_READ,
        share_mode=FILE_SHARE_READ,
        flags=flags,
    )


def _close_windows_handle(handle: int) -> None:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(wintypes.HANDLE(handle))


def _read_windows_reparse_data(handle: int) -> bytes:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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

    buffer = ctypes.create_string_buffer(MAXIMUM_REPARSE_DATA_BUFFER_SIZE)
    returned = wintypes.DWORD()
    if not device_io(
        wintypes.HANDLE(handle),
        FSCTL_GET_REPARSE_POINT,
        None,
        0,
        buffer,
        len(buffer),
        ctypes.byref(returned),
        None,
    ):
        error = ctypes.get_last_error()
        raise ctypes.WinError(error or 1)
    return bytes(buffer.raw[: returned.value])


@contextmanager
def _open_windows_reparse_point(path: Path, *, share_mode: int) -> Iterator[tuple[int, bytes]]:
    FILE_READ_EA = 0x00000008
    FILE_ATTRIBUTE_NORMAL = 0x00000080
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    handle = _create_windows_raw_handle(
        path,
        desired_access=FILE_READ_EA,
        share_mode=share_mode,
        flags=FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT,
    )
    try:
        final_norm = _final_path_from_handle(handle)
        _validate_final_path(path, final_norm, root=None, exact_path=True)
        yield handle, _read_windows_reparse_data(handle)
    finally:
        _close_windows_handle(handle)


def _app_execution_alias_identity(path: Path) -> Optional[ExecutableIdentity]:
    if os.name != "nt":
        return None
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    FILE_SHARE_DELETE = 0x00000004
    try:
        with _open_windows_reparse_point(
            path,
            share_mode=FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        ) as (_handle, data):
            if len(data) < 8 or int.from_bytes(data[:4], "little") != IO_REPARSE_TAG_APPEXECLINK:
                return None
            return ExecutableIdentity(
                kind=EXECUTABLE_IDENTITY_APP_EXECUTION_ALIAS,
                sha256=hashlib.sha256(data).hexdigest(),
            )
    except OSError as exc:
        if getattr(exc, "winerror", None) == ERROR_NOT_A_REPARSE_POINT:
            return None
        return None


@contextmanager
def _open_windows_deny_write_delete(path: Path) -> Iterator[BinaryIO]:
    """Open one regular file for read while denying competing write/delete opens."""
    import msvcrt

    handle = _create_windows_handle(path, directory=False)
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        fd = msvcrt.open_osfhandle(handle, flags)
    except Exception:
        _close_windows_handle(handle)
        raise

    with os.fdopen(fd, "rb", closefd=True) as stream:
        yield stream


@contextmanager
def guarded_open_read(
    path: Path | str,
    *,
    root: Optional[Path | str] = None,
    exact_path: bool = False,
    deny_write_delete: bool = False,
    expected_sha256: Optional[str] = None,
) -> Iterator[BinaryIO]:
    """Open once, validate the opened object, then consume that same handle."""
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = Path(os.path.abspath(str(requested)))

    opener = _open_windows_deny_write_delete(requested) if os.name == "nt" and deny_write_delete else requested.open("rb")
    with opener as stream:
        final_norm = _opened_final_path(stream)
        _validate_final_path(requested, final_norm, root=root, exact_path=exact_path)
        if expected_sha256 is not None:
            if not valid_sha256(expected_sha256):
                raise FileIdentityMismatch("expected file SHA-256 is malformed")
            actual = _hash_stream(stream)
            if not hmac.compare_digest(actual, expected_sha256.lower()):
                raise FileIdentityMismatch("current file bytes no longer match the reviewed fingerprint")
        yield stream


@contextmanager
def guarded_executable_identity(
    path: Path | str,
    *,
    expected_kind: str,
    expected_sha256: str,
) -> Iterator[None]:
    """Hold the reviewed executable identity stable through the caller's use."""
    if not valid_executable_identity(expected_kind, expected_sha256):
        raise FileIdentityMismatch("expected executable identity is malformed")
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = Path(os.path.abspath(str(requested)))

    if expected_kind == EXECUTABLE_IDENTITY_APP_EXECUTION_ALIAS:
        if os.name != "nt":
            raise FileIdentityMismatch("App Execution Alias identity is only valid on Windows")
        FILE_SHARE_READ = 0x00000001
        with _open_windows_reparse_point(requested, share_mode=FILE_SHARE_READ) as (_handle, data):
            if len(data) < 8 or int.from_bytes(data[:4], "little") != IO_REPARSE_TAG_APPEXECLINK:
                raise FileIdentityMismatch("resolved executable is no longer the reviewed App Execution Alias")
            actual = hashlib.sha256(data).hexdigest()
            if not hmac.compare_digest(actual, expected_sha256.lower()):
                raise FileIdentityMismatch("App Execution Alias identity no longer matches the reviewed plan")
            yield
        return

    with guarded_open_read(
        requested,
        exact_path=True,
        deny_write_delete=True,
        expected_sha256=expected_sha256,
    ):
        yield


@contextmanager
def guarded_directory(
    path: Path | str,
    *,
    root: Optional[Path | str] = None,
    exact_path: bool = False,
) -> Iterator[None]:
    """Hold a directory identity stable while path-based enumeration consumes it."""
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = Path(os.path.abspath(str(requested)))
    if os.name != "nt":
        final_norm = os.path.normcase(str(requested.resolve(strict=True)))
        _validate_final_path(requested, final_norm, root=root, exact_path=exact_path)
        yield
        return

    handle = _create_windows_handle(requested, directory=True)
    try:
        final_norm = _final_path_from_handle(handle)
        _validate_final_path(requested, final_norm, root=root, exact_path=exact_path)
        yield
    finally:
        _close_windows_handle(handle)


def executable_identity(path: Path | str) -> Optional[ExecutableIdentity]:
    """Fingerprint the actual object Windows will use as the executable entry point."""
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = Path(os.path.abspath(str(requested)))

    alias = _app_execution_alias_identity(requested)
    if alias is not None:
        return alias
    try:
        with guarded_open_read(requested, exact_path=True) as stream:
            return ExecutableIdentity(kind=EXECUTABLE_IDENTITY_FILE, sha256=_hash_stream(stream))
    except OSError:
        return None


def file_sha256(path: Path | str) -> Optional[str]:
    """Return a regular-file SHA-256 only when the exact opened path can be established."""
    identity = executable_identity(path)
    if identity is None or identity.kind != EXECUTABLE_IDENTITY_FILE:
        return None
    return identity.sha256
