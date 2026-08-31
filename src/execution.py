"""Bounded, identity-preserving subprocess execution for Windows Dev Agent."""

from __future__ import annotations

from collections import deque
import ctypes
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Any, BinaryIO, Optional

from src.file_guard import (
    EXECUTABLE_IDENTITY_FILE,
    EXECUTABLE_IDENTITY_POWERSHELL_SCRIPT,
    FileBoundaryError,
    FileIdentityMismatch,
    executable_identity,
    guarded_executable_identity,
    valid_executable_identity,
)

DEFAULT_STDOUT_BYTES = 8_000
DEFAULT_STDERR_BYTES = 4_000
_READ_CHUNK_BYTES = 8_192


_WINDOWS_COMMAND_TARGET_EXTENSIONS = (".exe", ".com", ".ps1")
_WINDOWS_BATCH_EXTENSIONS = {".bat", ".cmd"}


def _windows_executable_names(command: str) -> list[str]:
    """Return only command-target forms WDA can execute without cmd.exe parsing."""
    suffix = Path(command).suffix.casefold()
    if suffix:
        return [command] if suffix in _WINDOWS_COMMAND_TARGET_EXTENSIONS else []
    return [command + extension for extension in _WINDOWS_COMMAND_TARGET_EXTENSIONS]


def _windows_path_directories() -> list[Path]:
    """Return absolute PATH directories without any current-directory dependency."""
    raw_path = os.environ.get("PATH")
    if raw_path is None:
        raw_path = os.defpath
    try:
        current = Path.cwd().resolve()
    except OSError:
        current = Path.cwd().absolute()
    current_key = os.path.normcase(str(current))
    result: list[Path] = []
    seen: set[str] = set()
    for raw_entry in raw_path.split(os.pathsep):
        entry = raw_entry.strip().strip('"')
        if not entry:
            continue
        entry = os.path.expandvars(entry)
        directory = Path(entry).expanduser()
        # Relative PATH entries are necessarily cwd-dependent on Windows. They are
        # excluded rather than silently becoming project/plugin executable authority.
        if not directory.is_absolute():
            continue
        try:
            resolved = directory.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key == current_key or key in seen:
            continue
        seen.add(key)
        result.append(resolved)
    return result


def resolve_executable(command: str) -> Optional[str]:
    """Resolve one executable once without granting Windows cwd implicit search authority."""
    raw = str(command or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        if os.name == "nt" and candidate.suffix.casefold() not in _WINDOWS_COMMAND_TARGET_EXTENSIONS:
            return None
        try:
            return str(candidate.resolve()) if candidate.is_file() else None
        except OSError:
            return None
    if os.name == "nt":
        # Internal callers use bare executable names. Reject relative directory
        # components (including drive-relative forms) instead of interpreting them
        # against a host/project current directory.
        if candidate.drive or candidate.parent != Path("."):
            return None
        for directory in _windows_path_directories():
            for name in _windows_executable_names(raw):
                path = directory / name
                try:
                    if path.is_file():
                        return str(path.resolve())
                except OSError:
                    continue
        return None

    import shutil
    found = shutil.which(raw)
    if not found:
        return None
    try:
        path = Path(found).resolve()
    except OSError:
        return None
    return str(path) if path.is_file() else None


def executable_identity_matches(expected: str, actual: str) -> bool:
    """Return whether two values identify the same current absolute executable path."""
    if not isinstance(expected, str) or not isinstance(actual, str):
        return False
    try:
        expected_path = Path(expected).expanduser()
        actual_path = Path(actual).expanduser()
        if not expected_path.is_absolute() or not actual_path.is_absolute():
            return False
        expected_path = expected_path.resolve(strict=True)
        actual_path = actual_path.resolve(strict=True)
        if not expected_path.is_file() or not actual_path.is_file():
            return False
    except OSError:
        return False
    return os.path.normcase(str(expected_path)) == os.path.normcase(str(actual_path))


def _windows_directory() -> Optional[Path]:
    """Ask Windows for its installation directory instead of trusting PATH."""
    if os.name != "nt":
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32_768)
        length = ctypes.windll.kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    except (AttributeError, OSError):
        return None
    if not length or length >= len(buffer):
        return None
    return Path(buffer.value)


def resolve_windows_system_executable(*names: str) -> Optional[str]:
    """Resolve a Windows-owned control-plane executable from the native system tree."""
    windows_dir = _windows_directory()
    if windows_dir is None:
        return None
    system_dirs = []
    if os.environ.get("PROCESSOR_ARCHITEW6432"):
        system_dirs.append(windows_dir / "Sysnative")
    system_dirs.append(windows_dir / "System32")
    for directory in system_dirs:
        for name in names:
            candidate = directory / name
            try:
                if candidate.is_file():
                    return str(candidate.resolve())
            except OSError:
                continue
    return None


def _windows_powershell_interpreter():
    path = resolve_windows_system_executable(
        str(Path("WindowsPowerShell") / "v1.0" / "powershell.exe")
    )
    if path is None:
        return None, None
    identity = executable_identity(path)
    if identity is None or identity.kind != EXECUTABLE_IDENTITY_FILE:
        return None, None
    return path, identity


class _TailBuffer:
    def __init__(self, limit: int):
        self.limit = max(0, int(limit))
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        if not chunk or self.limit == 0:
            return
        with self._lock:
            if len(chunk) >= self.limit:
                self._chunks.clear()
                self._chunks.append(chunk[-self.limit :])
                self._size = self.limit
                return
            self._chunks.append(chunk)
            self._size += len(chunk)
            while self._size > self.limit and self._chunks:
                excess = self._size - self.limit
                first = self._chunks[0]
                if len(first) <= excess:
                    self._chunks.popleft()
                    self._size -= len(first)
                else:
                    self._chunks[0] = first[excess:]
                    self._size -= excess

    def text(self) -> str:
        with self._lock:
            snapshot = b"".join(self._chunks)
        return snapshot.decode("utf-8", errors="replace")


def _drain(stream: BinaryIO, tail: _TailBuffer) -> None:
    try:
        read_chunk = getattr(stream, "read1", stream.read)
        while True:
            chunk = read_chunk(_READ_CHUNK_BYTES)
            if not chunk:
                break
            tail.append(chunk)
    except (OSError, ValueError):
        return
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _join_readers(readers: list[threading.Thread], timeout: float) -> None:
    deadline = time.monotonic() + max(0.0, float(timeout))
    for reader in readers:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        reader.join(timeout=remaining)


def _cancel_synchronous_reader(reader: threading.Thread) -> bool:
    """Cancel one blocked synchronous Windows pipe read owned by reader."""
    if os.name != "nt" or not reader.is_alive():
        return False
    native_id = getattr(reader, "native_id", None)
    if not isinstance(native_id, int) or native_id <= 0:
        return False
    try:
        kernel32 = ctypes.windll.kernel32
        open_thread = kernel32.OpenThread
        cancel_synchronous_io = kernel32.CancelSynchronousIo
        close_handle = kernel32.CloseHandle
        open_thread.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        open_thread.restype = ctypes.c_void_p
        cancel_synchronous_io.argtypes = [ctypes.c_void_p]
        cancel_synchronous_io.restype = ctypes.c_int
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        handle = open_thread(0x0001, False, native_id)  # THREAD_TERMINATE
    except (AttributeError, OSError):
        return False
    if not handle:
        return False
    try:
        return bool(cancel_synchronous_io(handle))
    except OSError:
        return False
    finally:
        close_handle(handle)


def _settle_output_readers(readers: list[threading.Thread]) -> tuple[bool, bool]:
    """Return (clean_eof_complete, no_reader_remains_active)."""
    _join_readers(readers, 2.0)
    pending = [reader for reader in readers if reader.is_alive()]
    capture_complete = not pending
    if pending and os.name == "nt":
        for reader in pending:
            _cancel_synchronous_reader(reader)
        _join_readers(pending, 2.0)
    capture_settled = not any(reader.is_alive() for reader in readers)
    return capture_complete, capture_settled


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = resolve_windows_system_executable("taskkill.exe")
        if taskkill:
            cleanup_launch = launch_bound(
                [taskkill, "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            cleanup_process = cleanup_launch.get("process")
            if cleanup_process is not None:
                try:
                    cleanup_process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    try:
                        cleanup_process.kill()
                    except OSError:
                        pass
                    try:
                        cleanup_process.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def launch_bound(
    argv: list[str],
    *,
    cwd: Optional[Path] = None,
    expected_executable_identity_kind: Optional[str] = None,
    expected_executable_identity_sha256: Optional[str] = None,
    stdin: Any = subprocess.DEVNULL,
    stdout: Any = None,
    stderr: Any = None,
    creationflags: int = 0,
) -> dict[str, Any]:
    """Start one absolute command target while optionally sealing its reviewed identity."""
    if not argv or not isinstance(argv[0], str):
        return {"succeeded": False, "error": "argv must contain a command target", "argv": argv, "execution_started": False}
    executable = Path(argv[0]).expanduser()
    if not executable.is_absolute():
        return {
            "succeeded": False,
            "error": "Command-target identity must be resolved to an absolute path before execution",
            "argv": argv,
            "execution_started": False,
        }
    if os.name == "nt" and executable.suffix.casefold() not in _WINDOWS_COMMAND_TARGET_EXTENSIONS:
        return {
            "succeeded": False,
            "error": f"Unsupported Windows command target: {executable.suffix or '<no extension>'}",
            "argv": argv,
            "execution_started": False,
        }
    try:
        if not executable.is_file():
            return {"succeeded": False, "error": f"Executable is not a file: {executable}", "argv": argv, "execution_started": False}
    except OSError as exc:
        return {"succeeded": False, "error": str(exc), "argv": argv, "execution_started": False}

    identity_requested = expected_executable_identity_kind is not None or expected_executable_identity_sha256 is not None
    if identity_requested:
        if not valid_executable_identity(
            expected_executable_identity_kind,
            expected_executable_identity_sha256,
        ):
            return {
                "succeeded": False,
                "error": "expected executable identity is malformed",
                "argv": argv,
                "execution_started": False,
                "identity_invalid": True,
            }
        effective_identity_kind = expected_executable_identity_kind
        effective_identity_sha256 = expected_executable_identity_sha256
    else:
        current_identity = executable_identity(executable)
        if current_identity is None:
            return {
                "succeeded": False,
                "error": "Executable identity could not be established at launch",
                "argv": argv,
                "execution_started": False,
                "identity_unavailable": True,
            }
        effective_identity_kind = current_identity.kind
        effective_identity_sha256 = current_identity.sha256

    is_powershell_script = os.name == "nt" and executable.suffix.casefold() == ".ps1"
    if is_powershell_script != (effective_identity_kind == EXECUTABLE_IDENTITY_POWERSHELL_SCRIPT):
        return {
            "succeeded": False,
            "error": "Current command-target type no longer matches the reviewed identity kind",
            "argv": argv,
            "execution_started": False,
            "identity_mismatch": True,
        }

    launch_argv = argv
    interpreter_path = None
    interpreter_identity = None
    if is_powershell_script:
        interpreter_path, interpreter_identity = _windows_powershell_interpreter()
        if interpreter_path is None or interpreter_identity is None:
            return {
                "succeeded": False,
                "error": "Windows PowerShell interpreter identity could not be established",
                "argv": argv,
                "execution_started": False,
                "identity_unavailable": True,
            }
        launch_argv = [
            interpreter_path,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(executable),
            *argv[1:],
        ]

    def spawn(actual_argv: list[str]) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            actual_argv,
            cwd=str(cwd) if cwd else None,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            shell=False,
            creationflags=creationflags,
        )

    try:
        # Every launch seals the reviewed command target through process creation.
        # PowerShell-script targets additionally seal the Windows-owned interpreter
        # while it starts; cmd.exe is never used for target argument parsing.
        with guarded_executable_identity(
            executable,
            expected_kind=effective_identity_kind,
            expected_sha256=effective_identity_sha256,
        ):
            if interpreter_path is not None and interpreter_identity is not None:
                try:
                    with guarded_executable_identity(
                        interpreter_path,
                        expected_kind=interpreter_identity.kind,
                        expected_sha256=interpreter_identity.sha256,
                    ):
                        process = spawn(launch_argv)
                except (FileIdentityMismatch, FileBoundaryError) as exc:
                    return {
                        "succeeded": False,
                        "error": f"Windows PowerShell interpreter changed before launch: {exc}",
                        "argv": argv,
                        "execution_started": False,
                        "runtime_identity_mismatch": True,
                    }
            else:
                process = spawn(launch_argv)
    except (FileIdentityMismatch, FileBoundaryError) as exc:
        return {
            "succeeded": False,
            "error": str(exc),
            "argv": argv,
            "execution_started": False,
            "identity_mismatch": True,
        }
    except OSError as exc:
        return {"succeeded": False, "error": str(exc), "argv": argv, "execution_started": False}
    return {"process": process, "argv": argv, "execution_started": True}


def run_bounded(
    argv: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int = 30,
    stdout_bytes: int = DEFAULT_STDOUT_BYTES,
    stderr_bytes: int = DEFAULT_STDERR_BYTES,
    expected_executable_identity_kind: Optional[str] = None,
    expected_executable_identity_sha256: Optional[str] = None,
) -> dict[str, Any]:
    """Execute an absolute typed command target while retaining bounded output tails."""
    stdout_tail = _TailBuffer(stdout_bytes)
    stderr_tail = _TailBuffer(stderr_bytes)
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    launch = launch_bound(
        argv,
        cwd=cwd,
        expected_executable_identity_kind=expected_executable_identity_kind,
        expected_executable_identity_sha256=expected_executable_identity_sha256,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creationflags,
    )
    process = launch.pop("process", None)
    if process is None:
        return launch

    assert process.stdout is not None and process.stderr is not None
    reader_suffix = str(getattr(process, "pid", id(process)))
    readers = [
        threading.Thread(
            target=_drain,
            args=(process.stdout, stdout_tail),
            name=f"wda-stdout-drain-{reader_suffix}",
            daemon=True,
        ),
        threading.Thread(
            target=_drain,
            args=(process.stderr, stderr_tail),
            name=f"wda-stderr-drain-{reader_suffix}",
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    error: Optional[str] = None
    try:
        returncode = process.wait(timeout=max(1, int(timeout)))
    except subprocess.TimeoutExpired:
        timed_out = True
        error = f"Process timed out after {max(1, int(timeout))} seconds"
        _terminate_process_tree(process)
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = None
    finally:
        output_capture_complete, output_capture_settled = _settle_output_readers(readers)

    result: dict[str, Any] = {
        "succeeded": returncode == 0 and not timed_out,
        "returncode": returncode,
        "stdout": stdout_tail.text(),
        "stderr": stderr_tail.text(),
        "argv": argv,
        "execution_started": True,
        "output_capture_complete": output_capture_complete,
        "output_capture_settled": output_capture_settled,
    }
    if timed_out:
        result["timed_out"] = True
        result["error"] = error
    return result
