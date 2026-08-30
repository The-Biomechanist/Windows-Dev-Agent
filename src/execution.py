"""Bounded, identity-preserving subprocess execution for Windows Dev Agent."""

from __future__ import annotations

from collections import deque
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any, BinaryIO, Optional

DEFAULT_STDOUT_BYTES = 8_000
DEFAULT_STDERR_BYTES = 4_000
_READ_CHUNK_BYTES = 8_192


def resolve_executable(command: str) -> Optional[str]:
    """Resolve one executable once and return the exact absolute path to execute."""
    raw = str(command or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    if candidate.is_absolute():
        try:
            return str(candidate.resolve()) if candidate.is_file() else None
        except OSError:
            return None
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


class _TailBuffer:
    def __init__(self, limit: int):
        self.limit = max(0, int(limit))
        self._chunks: deque[bytes] = deque()
        self._size = 0

    def append(self, chunk: bytes) -> None:
        if not chunk or self.limit == 0:
            return
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
        return b"".join(self._chunks).decode("utf-8", errors="replace")


def _drain(stream: BinaryIO, tail: _TailBuffer) -> None:
    try:
        while True:
            chunk = stream.read(_READ_CHUNK_BYTES)
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


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = resolve_windows_system_executable("taskkill.exe")
        if taskkill:
            try:
                subprocess.run(
                    [taskkill, "/PID", str(process.pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    check=False,
                    timeout=10,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def run_bounded(
    argv: list[str],
    *,
    cwd: Optional[Path] = None,
    timeout: int = 30,
    stdout_bytes: int = DEFAULT_STDOUT_BYTES,
    stderr_bytes: int = DEFAULT_STDERR_BYTES,
) -> dict[str, Any]:
    """Execute an absolute argv[0] while retaining only bounded stdout/stderr tails."""
    if not argv or not isinstance(argv[0], str):
        return {"succeeded": False, "error": "argv must contain an executable", "argv": argv, "execution_started": False}
    executable = Path(argv[0]).expanduser()
    if not executable.is_absolute():
        return {
            "succeeded": False,
            "error": "Executable identity must be resolved to an absolute path before execution",
            "argv": argv,
            "execution_started": False,
        }
    try:
        if not executable.is_file():
            return {"succeeded": False, "error": f"Executable is not a file: {executable}", "argv": argv, "execution_started": False}
    except OSError as exc:
        return {"succeeded": False, "error": str(exc), "argv": argv, "execution_started": False}

    stdout_tail = _TailBuffer(stdout_bytes)
    stderr_tail = _TailBuffer(stderr_bytes)
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            creationflags=creationflags,
        )
    except OSError as exc:
        return {"succeeded": False, "error": str(exc), "argv": argv, "execution_started": False}

    assert process.stdout is not None and process.stderr is not None
    readers = [
        threading.Thread(target=_drain, args=(process.stdout, stdout_tail), daemon=True),
        threading.Thread(target=_drain, args=(process.stderr, stderr_tail), daemon=True),
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
        for reader in readers:
            reader.join(timeout=2)

    result: dict[str, Any] = {
        "succeeded": returncode == 0 and not timed_out,
        "returncode": returncode,
        "stdout": stdout_tail.text(),
        "stderr": stderr_tail.text(),
        "argv": argv,
        "execution_started": True,
    }
    if timed_out:
        result["timed_out"] = True
        result["error"] = error
    return result
