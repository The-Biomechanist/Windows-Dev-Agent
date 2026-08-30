from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one source match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# src/execution.py — bind reviewed identity to both canonical path and SHA-256,
# and on Windows hold a no-write/no-delete sharing handle through process launch.
replace_once(
    "src/execution.py",
    '''from collections import deque
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any, BinaryIO, Optional
''',
    '''from collections import deque
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import hmac
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any, BinaryIO, Iterator, Optional
''',
)
replace_once(
    "src/execution.py",
    '''DEFAULT_STDOUT_BYTES = 8_000
DEFAULT_STDERR_BYTES = 4_000
_READ_CHUNK_BYTES = 8_192
''',
    '''DEFAULT_STDOUT_BYTES = 8_000
DEFAULT_STDERR_BYTES = 4_000
_READ_CHUNK_BYTES = 8_192
_FINGERPRINT_CHUNK_BYTES = 1024 * 1024
_FINGERPRINT_PREFIX = "sha256:"
''',
)
replace_once(
    "src/execution.py",
    '''def executable_identity_matches(expected: str, actual: str) -> bool:
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
''',
    '''def _resolved_absolute_file(path: str) -> Optional[Path]:
    if not isinstance(path, str) or not path.strip():
        return None
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return None
    try:
        resolved = candidate.resolve(strict=True)
        return resolved if resolved.is_file() else None
    except OSError:
        return None


def _normalized_absolute_path(path: str) -> Optional[str]:
    if not isinstance(path, str) or not path.strip():
        return None
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return None
    return os.path.normcase(os.path.normpath(str(candidate)))


def _hash_resolved_executable(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_FINGERPRINT_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return _FINGERPRINT_PREFIX + digest.hexdigest()


@contextmanager
def _windows_executable_read_guard(path: Path) -> Iterator[None]:
    """Prevent write/delete replacement of one executable while its identity is in use."""
    if os.name != "nt":
        yield
        return

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    close_handle = ctypes.windll.kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    generic_read = 0x80000000
    file_share_read = 0x00000001
    open_existing = 3
    file_attribute_normal = 0x00000080
    invalid_handle_value = ctypes.c_void_p(-1).value

    handle = create_file(
        str(path),
        generic_read,
        file_share_read,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    if not handle or handle == invalid_handle_value:
        raise ctypes.WinError()
    try:
        yield
    finally:
        close_handle(handle)


def executable_fingerprint(path: str) -> Optional[str]:
    """Return a stable SHA-256 identity for one absolute executable file."""
    resolved = _resolved_absolute_file(path)
    if resolved is None:
        return None
    try:
        with _windows_executable_read_guard(resolved):
            return _hash_resolved_executable(resolved)
    except OSError:
        return None


@contextmanager
def executable_identity_guard(
    expected_path: str,
    expected_fingerprint: str,
    actual_path: str,
) -> Iterator[str]:
    """Verify reviewed path+content identity and pin it against Windows replacement."""
    expected_normalized = _normalized_absolute_path(expected_path)
    actual_resolved = _resolved_absolute_file(actual_path)
    if expected_normalized is None or actual_resolved is None:
        raise ValueError("Executable identity could not be established")
    if os.path.normcase(os.path.normpath(str(actual_resolved))) != expected_normalized:
        raise ValueError("Executable path no longer matches the reviewed plan")
    if not isinstance(expected_fingerprint, str) or not expected_fingerprint.startswith(_FINGERPRINT_PREFIX):
        raise ValueError("Reviewed executable fingerprint is missing or malformed")

    try:
        with _windows_executable_read_guard(actual_resolved):
            actual_fingerprint = _hash_resolved_executable(actual_resolved)
            if not hmac.compare_digest(expected_fingerprint, actual_fingerprint):
                raise ValueError("Executable contents no longer match the reviewed plan")
            yield str(actual_resolved)
    except OSError as exc:
        raise ValueError(f"Executable identity could not be locked: {exc}") from exc


def executable_identity_matches(expected_path: str, expected_fingerprint: str, actual_path: str) -> bool:
    """Return whether current path+content identity matches the reviewed executable."""
    try:
        with executable_identity_guard(expected_path, expected_fingerprint, actual_path):
            return True
    except ValueError:
        return False
''',
)

# src/capabilities.py — plan and execute carry the content fingerprint.
replace_once(
    "src/capabilities.py",
    '''from src.execution import executable_identity_matches, resolve_executable, run_bounded
''',
    '''from src.execution import executable_fingerprint, executable_identity_guard, resolve_executable, run_bounded
''',
)
replace_once(
    "src/capabilities.py",
    '''    expected_executable: Optional[str] = None,
    path: Optional[Path] = None,
''',
    '''    expected_executable: Optional[str] = None,
    expected_executable_fingerprint: Optional[str] = None,
    path: Optional[Path] = None,
''',
)
replace_once(
    "src/capabilities.py",
    '''    safety_class = effective_safety(capability, extra_args)
    argv = [*tool.argv, *extra_args]
    plan: dict[str, Any] = {
''',
    '''    safety_class = effective_safety(capability, extra_args)
    argv = [*tool.argv, *extra_args]
    fingerprint = executable_fingerprint(tool.argv[0])
    if fingerprint is None:
        return {
            "status": "unavailable",
            "capability": capability.id,
            "description": capability.description,
            "safety_class": safety_class,
            "executable": tool.argv[0],
            "error": "Executable content identity could not be established",
            "execution_started": False,
        }
    plan: dict[str, Any] = {
''',
)
replace_once(
    "src/capabilities.py",
    '''        "tool": tool.name,
        "executable": tool.argv[0],
        "argv": argv,
''',
    '''        "tool": tool.name,
        "executable": tool.argv[0],
        "executable_fingerprint": fingerprint,
        "argv": argv,
''',
)
replace_once(
    "src/capabilities.py",
    '''    if not isinstance(expected_executable, str) or not expected_executable.strip():
        return {
            **plan,
            "status": "invalid_input",
            "error": "expected_executable from the reviewed plan is required for execution",
            "execution_started": False,
        }
    if not executable_identity_matches(expected_executable, tool.argv[0]):
        return {
            **plan,
            "status": "stale_plan",
            "error": "Resolved executable no longer matches the reviewed plan; obtain a fresh plan before execution",
            "execution_started": False,
        }

    run_cwd: Optional[Path] = None
''',
    '''    if (
        not isinstance(expected_executable, str)
        or not expected_executable.strip()
        or not isinstance(expected_executable_fingerprint, str)
        or not expected_executable_fingerprint.strip()
    ):
        return {
            **plan,
            "status": "invalid_input",
            "error": "expected_executable and expected_executable_fingerprint from the reviewed plan are required for execution",
            "execution_started": False,
        }

    run_cwd: Optional[Path] = None
''',
)
replace_once(
    "src/capabilities.py",
    '''    result = run_bounded(
        argv,
        cwd=run_cwd,
        timeout=max(1, min(int(timeout_seconds), 600)),
    )
''',
    '''    try:
        with executable_identity_guard(
            expected_executable,
            expected_executable_fingerprint,
            tool.argv[0],
        ):
            result = run_bounded(
                argv,
                cwd=run_cwd,
                timeout=max(1, min(int(timeout_seconds), 600)),
            )
    except ValueError as exc:
        return {
            **plan,
            "status": "stale_plan",
            "error": f"{exc}; obtain a fresh plan before execution",
            "execution_started": False,
        }
''',
)

# src/mcp/server.py — schemas, plan fingerprints, and guarded execution.
replace_once(
    "src/mcp/server.py",
    '''from src.execution import (
    executable_identity_matches,
    resolve_executable,
''',
    '''from src.execution import (
    executable_fingerprint,
    executable_identity_guard,
    resolve_executable,
''',
)
replace_once(
    "src/mcp/server.py",
    '''def _expected_executable_property() -> dict[str, Any]:
    return {
        "type": "string",
        "maxLength": 4096,
        "description": "Absolute executable path returned by the reviewed execute=false plan; required when execute=true",
    }


TOOLS = [
''',
    '''def _expected_executable_property() -> dict[str, Any]:
    return {
        "type": "string",
        "maxLength": 4096,
        "description": "Absolute executable path returned by the reviewed execute=false plan; required when execute=true",
    }


def _expected_executable_fingerprint_property() -> dict[str, Any]:
    return {
        "type": "string",
        "maxLength": 71,
        "description": "SHA-256 executable fingerprint returned by the reviewed execute=false plan; required when execute=true",
    }


TOOLS = [
''',
)
replace_once(
    "src/mcp/server.py",
    '''                "expected_executable": _expected_executable_property(),
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600, "default": 120},
''',
    '''                "expected_executable": _expected_executable_property(),
                "expected_executable_fingerprint": _expected_executable_fingerprint_property(),
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600, "default": 120},
''',
)
replace_once(
    "src/mcp/server.py",
    '''                "execute": _bool_property("Execute the reviewed install plan"),
                "expected_executable": _expected_executable_property(),
''',
    '''                "execute": _bool_property("Execute the reviewed install plan"),
                "expected_executable": _expected_executable_property(),
                "expected_executable_fingerprint": _expected_executable_fingerprint_property(),
''',
)
replace_once(
    "src/mcp/server.py",
    '''                "execute": _bool_property("Launch the reviewed route"),
                "expected_executable": _expected_executable_property(),
''',
    '''                "execute": _bool_property("Launch the reviewed route"),
                "expected_executable": _expected_executable_property(),
                "expected_executable_fingerprint": _expected_executable_fingerprint_property(),
''',
)
replace_once(
    "src/mcp/server.py",
    '''        expected_executable=args.get("expected_executable"),
    )
''',
    '''        expected_executable=args.get("expected_executable"),
        expected_executable_fingerprint=args.get("expected_executable_fingerprint"),
    )
''',
)
replace_once(
    "src/mcp/server.py",
    '''    resolved = _resolve_argv(configured)
    argv = resolved or configured
    plan = {
''',
    '''    resolved = _resolve_argv(configured)
    argv = resolved or configured
    fingerprint = executable_fingerprint(resolved[0]) if resolved else None
    plan = {
''',
)
replace_once(
    "src/mcp/server.py",
    '''        "safety_class": "approval-required",
        "executable": resolved[0] if resolved else None,
        "argv": argv,
''',
    '''        "safety_class": "approval-required",
        "executable": resolved[0] if resolved else None,
        "executable_fingerprint": fingerprint,
        "argv": argv,
''',
)
replace_once(
    "src/mcp/server.py",
    '''    if args.get("execute") is not True:
        return plan
    if resolved is None:
        return {**plan, "status": "unavailable", "error": f"{configured[0]} is not installed", "execution_started": False}
    expected_executable = args.get("expected_executable")
    if not isinstance(expected_executable, str) or not expected_executable.strip():
        return {
            **plan,
            "status": "invalid_input",
            "error": "expected_executable from the reviewed plan is required for execution",
            "execution_started": False,
        }
    if not executable_identity_matches(expected_executable, resolved[0]):
        return {
            **plan,
            "status": "stale_plan",
            "error": "Resolved executable no longer matches the reviewed plan; obtain a fresh plan before execution",
            "execution_started": False,
        }

    cache_invalidated = invalidate_environment_cache(DATA_DIR)
    if not cache_invalidated:
        return {
            **plan,
            "status": "failed",
            "error": "Environment cache invalidation could not be established; installer was not started",
            "execution_started": False,
            "environment_cache_invalidated": False,
        }
    result = run_bounded(resolved, timeout=600)
''',
    '''    if args.get("execute") is not True:
        if resolved is not None and fingerprint is None:
            return {**plan, "status": "unavailable", "error": "Executable content identity could not be established", "execution_started": False}
        return plan
    if resolved is None:
        return {**plan, "status": "unavailable", "error": f"{configured[0]} is not installed", "execution_started": False}
    expected_executable = args.get("expected_executable")
    expected_fingerprint = args.get("expected_executable_fingerprint")
    if (
        not isinstance(expected_executable, str)
        or not expected_executable.strip()
        or not isinstance(expected_fingerprint, str)
        or not expected_fingerprint.strip()
    ):
        return {
            **plan,
            "status": "invalid_input",
            "error": "expected_executable and expected_executable_fingerprint from the reviewed plan are required for execution",
            "execution_started": False,
        }

    try:
        with executable_identity_guard(expected_executable, expected_fingerprint, resolved[0]):
            cache_invalidated = invalidate_environment_cache(DATA_DIR)
            if not cache_invalidated:
                return {
                    **plan,
                    "status": "failed",
                    "error": "Environment cache invalidation could not be established; installer was not started",
                    "execution_started": False,
                    "environment_cache_invalidated": False,
                }
            result = run_bounded(resolved, timeout=600)
    except ValueError as exc:
        return {
            **plan,
            "status": "stale_plan",
            "error": f"{exc}; obtain a fresh plan before execution",
            "execution_started": False,
        }
''',
)
replace_once(
    "src/mcp/server.py",
    '''    plan = {
        "status": "planned",
        "environment": environment,
''',
    '''    fingerprint = executable_fingerprint(executable)
    plan = {
        "status": "planned",
        "environment": environment,
''',
)
replace_once(
    "src/mcp/server.py",
    '''        "safety_class": "approval-required",
        "executable": executable,
        "argv": argv,
''',
    '''        "safety_class": "approval-required",
        "executable": executable,
        "executable_fingerprint": fingerprint,
        "argv": argv,
''',
)
replace_once(
    "src/mcp/server.py",
    '''    if args.get("execute") is not True:
        return plan

    expected_executable = args.get("expected_executable")
    if not isinstance(expected_executable, str) or not expected_executable.strip():
        return {
            **plan,
            "status": "invalid_input",
            "error": "expected_executable from the reviewed plan is required for execution",
            "execution_started": False,
        }
    if not executable_identity_matches(expected_executable, executable):
        return {
            **plan,
            "status": "stale_plan",
            "error": "Resolved executable no longer matches the reviewed plan; obtain a fresh plan before execution",
            "execution_started": False,
        }

    if environment == "windows_sandbox":
''',
    '''    if args.get("execute") is not True:
        if fingerprint is None:
            return {**plan, "status": "unavailable", "error": "Executable content identity could not be established", "execution_started": False}
        return plan

    expected_executable = args.get("expected_executable")
    expected_fingerprint = args.get("expected_executable_fingerprint")
    if (
        not isinstance(expected_executable, str)
        or not expected_executable.strip()
        or not isinstance(expected_fingerprint, str)
        or not expected_fingerprint.strip()
    ):
        return {
            **plan,
            "status": "invalid_input",
            "error": "expected_executable and expected_executable_fingerprint from the reviewed plan are required for execution",
            "execution_started": False,
        }

    try:
        with executable_identity_guard(expected_executable, expected_fingerprint, executable):
            pass
    except ValueError as exc:
        return {
            **plan,
            "status": "stale_plan",
            "error": f"{exc}; obtain a fresh plan before execution",
            "execution_started": False,
        }

    if environment == "windows_sandbox":
''',
)
replace_once(
    "src/mcp/server.py",
    '''        try:
            process = subprocess.Popen(launch_argv, cwd=str(workspace), stdin=subprocess.DEVNULL, shell=False)
        except OSError as exc:
            shutil.rmtree(bundle_root, ignore_errors=True)
            return {**plan, "status": "failed", "error": str(exc), "execution_started": False, "cleanup_performed": True}
''',
    '''        try:
            with executable_identity_guard(expected_executable, expected_fingerprint, executable):
                process = subprocess.Popen(launch_argv, cwd=str(workspace), stdin=subprocess.DEVNULL, shell=False)
        except ValueError as exc:
            shutil.rmtree(bundle_root, ignore_errors=True)
            return {
                **plan,
                "status": "stale_plan",
                "error": f"{exc}; obtain a fresh plan before execution",
                "execution_started": False,
                "cleanup_performed": True,
            }
        except OSError as exc:
            shutil.rmtree(bundle_root, ignore_errors=True)
            return {**plan, "status": "failed", "error": str(exc), "execution_started": False, "cleanup_performed": True}
''',
)
replace_once(
    "src/mcp/server.py",
    '''    result = run_bounded(argv, cwd=workspace, timeout=600)
    return {**plan, **result, "status": "completed" if result.get("succeeded") else "failed"}
''',
    '''    try:
        with executable_identity_guard(expected_executable, expected_fingerprint, executable):
            result = run_bounded(argv, cwd=workspace, timeout=600)
    except ValueError as exc:
        return {
            **plan,
            "status": "stale_plan",
            "error": f"{exc}; obtain a fresh plan before execution",
            "execution_started": False,
        }
    return {**plan, **result, "status": "completed" if result.get("succeeded") else "failed"}
''',
)

# Core execution tests.
replace_once(
    "tests/test_execution.py",
    '''from src.execution import (
    executable_identity_matches,
    resolve_executable,
''',
    '''from src.execution import (
    executable_fingerprint,
    executable_identity_guard,
    executable_identity_matches,
    resolve_executable,
''',
)
replace_once(
    "tests/test_execution.py",
    '''def test_reviewed_executable_identity_requires_same_live_absolute_file(tmp_path: Path):
    executable = tmp_path / "tool.exe"
    executable.write_bytes(b"tool")
    other = tmp_path / "other.exe"
    other.write_bytes(b"other")

    assert executable_identity_matches(str(executable), str(executable)) is True
    assert executable_identity_matches(str(executable), str(other)) is False
    assert executable_identity_matches("tool.exe", str(executable)) is False
    assert executable_identity_matches(str(tmp_path / "missing.exe"), str(executable)) is False
''',
    '''def test_reviewed_executable_identity_binds_path_and_contents(tmp_path: Path):
    executable = tmp_path / "tool.exe"
    executable.write_bytes(b"tool")
    other = tmp_path / "other.exe"
    other.write_bytes(b"other")
    fingerprint = executable_fingerprint(str(executable))
    assert fingerprint is not None and fingerprint.startswith("sha256:")

    assert executable_identity_matches(str(executable), fingerprint, str(executable)) is True
    assert executable_identity_matches(str(executable), fingerprint, str(other)) is False
    assert executable_identity_matches("tool.exe", fingerprint, str(executable)) is False
    assert executable_identity_matches(str(tmp_path / "missing.exe"), fingerprint, str(executable)) is False

    executable.write_bytes(b"replacement")
    assert executable_identity_matches(str(executable), fingerprint, str(executable)) is False


@pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing identity guard is Windows-specific")
def test_windows_identity_guard_blocks_same_path_replacement_and_allows_process_launch(tmp_path: Path):
    source = Path(sys.executable).resolve()
    copy = tmp_path / "python.exe"
    copy.write_bytes(source.read_bytes())
    copy_fingerprint = executable_fingerprint(str(copy))
    assert copy_fingerprint is not None

    with executable_identity_guard(str(copy), copy_fingerprint, str(copy)):
        with pytest.raises(OSError):
            copy.write_bytes(b"replacement")
        result = run_bounded([str(copy), "-I", "-c", "print('guard-ok')"], timeout=10)
        assert result["succeeded"] is True

    copy.write_bytes(b"replacement")
''',
)

# Capability test helper/calls.
replace_once(
    "tests/test_capabilities_runtime.py",
    '''def _reviewed_executable(catalog: Path) -> str:
    return run_capability("probe", execute=False, path=catalog)["executable"]
''',
    '''def _reviewed_identity(catalog: Path) -> tuple[str, str]:
    plan = run_capability("probe", execute=False, path=catalog)
    return plan["executable"], plan["executable_fingerprint"]
''',
)
replace_once(
    "tests/test_capabilities_runtime.py",
    '''    assert result["executable"] == result["argv"][0]
    assert Path(result["argv"][0]).is_absolute()
''',
    '''    assert result["executable"] == result["argv"][0]
    assert result["executable_fingerprint"].startswith("sha256:")
    assert Path(result["argv"][0]).is_absolute()
''',
)
text = Path("tests/test_capabilities_runtime.py").read_text(encoding="utf-8")
text = text.replace(
    '''    expected = _reviewed_executable(catalog)
    result = run_capability("probe", execute=True, expected_executable=expected, path=catalog)
''',
    '''    expected, fingerprint = _reviewed_identity(catalog)
    result = run_capability("probe", execute=True, expected_executable=expected, expected_executable_fingerprint=fingerprint, path=catalog)
''',
)
text = text.replace(
    '''    stale = run_capability("probe", execute=True, expected_executable=str(tmp_path / "other.exe"), path=catalog)
''',
    '''    expected, fingerprint = _reviewed_identity(catalog)
    missing_fingerprint = run_capability("probe", execute=True, expected_executable=expected, path=catalog)
    assert missing_fingerprint["status"] == "invalid_input"
    stale = run_capability("probe", execute=True, expected_executable=str(tmp_path / "other.exe"), expected_executable_fingerprint=fingerprint, path=catalog)
''',
)
text = text.replace(
    'result = run_capability("probe", execute=True, expected_executable=_reviewed_executable(catalog), path=catalog)',
    'expected, fingerprint = _reviewed_identity(catalog)\n    result = run_capability("probe", execute=True, expected_executable=expected, expected_executable_fingerprint=fingerprint, path=catalog)',
)
if "_reviewed_executable" in text:
    raise SystemExit("tests/test_capabilities_runtime.py: stale _reviewed_executable reference remains")
Path("tests/test_capabilities_runtime.py").write_text(text, encoding="utf-8")

# MCP test surfaces. Use a lightweight fake context manager for identity guards.
replace_once(
    "tests/test_mcp_runtime.py",
    '''        assert "expected_executable" in properties
''',
    '''        assert "expected_executable" in properties
        assert "expected_executable_fingerprint" in properties
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''    assert observed["expected_executable"] is None
''',
    '''    assert observed["expected_executable"] is None
    assert observed["expected_executable_fingerprint"] is None
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''    monkeypatch.setattr(server, "resolve_executable", lambda _name: trusted)
    monkeypatch.setattr(server, "executable_identity_matches", lambda expected, actual: expected == actual)
    monkeypatch.setattr(server, "invalidate_environment_cache", lambda data_dir: observed.setdefault("invalidated", data_dir) is not None)
''',
    '''    fingerprint = "sha256:" + "a" * 64
    monkeypatch.setattr(server, "resolve_executable", lambda _name: trusted)
    monkeypatch.setattr(server, "executable_fingerprint", lambda _path: fingerprint)
    monkeypatch.setattr(server, "executable_identity_guard", lambda expected, expected_fp, actual: __import__("contextlib").nullcontext(actual) if expected == actual and expected_fp == fingerprint else (_ for _ in ()).throw(ValueError("stale")))
    monkeypatch.setattr(server, "invalidate_environment_cache", lambda data_dir: observed.setdefault("invalidated", data_dir) is not None)
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''            {"package_id": "Python.Python.3.12", "source": "winget", "execute": True, "expected_executable": trusted}
''',
    '''            {"package_id": "Python.Python.3.12", "source": "winget", "execute": True, "expected_executable": trusted, "expected_executable_fingerprint": fingerprint}
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''    monkeypatch.setattr(server, "resolve_executable", lambda _name: trusted)
    monkeypatch.setattr(server, "invalidate_environment_cache", lambda _data_dir: (_ for _ in ()).throw(AssertionError("plan must not invalidate")))
''',
    '''    fingerprint = "sha256:" + "b" * 64
    monkeypatch.setattr(server, "resolve_executable", lambda _name: trusted)
    monkeypatch.setattr(server, "executable_fingerprint", lambda _path: fingerprint)
    monkeypatch.setattr(server, "invalidate_environment_cache", lambda _data_dir: (_ for _ in ()).throw(AssertionError("plan must not invalidate")))
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''    assert result["executable"] == trusted
    assert result["argv"][0] == trusted
''',
    '''    assert result["executable"] == trusted
    assert result["executable_fingerprint"] == fingerprint
    assert result["argv"][0] == trusted
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''    monkeypatch.setattr(server, "_wsl_executable", lambda: trusted)
    monkeypatch.setattr(server, "executable_identity_matches", lambda expected, actual: expected == actual)
    monkeypatch.setattr(server, "run_bounded", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale plan must not execute")))
''',
    '''    fingerprint = "sha256:" + "c" * 64
    monkeypatch.setattr(server, "_wsl_executable", lambda: trusted)
    monkeypatch.setattr(server, "executable_fingerprint", lambda _path: fingerprint)
    monkeypatch.setattr(server, "executable_identity_guard", lambda expected, expected_fp, actual: __import__("contextlib").nullcontext(actual) if expected == actual and expected_fp == fingerprint else (_ for _ in ()).throw(ValueError("stale")))
    monkeypatch.setattr(server, "run_bounded", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("stale plan must not execute")))
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''    stale = run(server.handle_sandbox_run({**base, "expected_executable": "C:\\other\\wsl.exe"}))
''',
    '''    missing_fingerprint = run(server.handle_sandbox_run({**base, "expected_executable": trusted}))
    assert missing_fingerprint["status"] == "invalid_input"
    stale = run(server.handle_sandbox_run({**base, "expected_executable": "C:\\other\\wsl.exe", "expected_executable_fingerprint": fingerprint}))
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''    monkeypatch.setattr(server, "_windows_sandbox_executable", lambda: trusted)
    missing = run(
''',
    '''    fingerprint = "sha256:" + "d" * 64
    monkeypatch.setattr(server, "_windows_sandbox_executable", lambda: trusted)
    monkeypatch.setattr(server, "executable_fingerprint", lambda _path: fingerprint)
    missing = run(
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''    assert planned["executable"] == trusted
''',
    '''    assert planned["executable"] == trusted
    assert planned["executable_fingerprint"] == fingerprint
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''    monkeypatch.setattr(server, "_wsl_executable", lambda: trusted_wsl)
    result = run(
''',
    '''    fingerprint = "sha256:" + "e" * 64
    monkeypatch.setattr(server, "_wsl_executable", lambda: trusted_wsl)
    monkeypatch.setattr(server, "executable_fingerprint", lambda _path: fingerprint)
    result = run(
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''    assert result["executable"] == trusted_wsl
    assert result["argv"][:4] == [trusted_wsl, "--cd", str(tmp_path.resolve()), "--"]
''',
    '''    assert result["executable"] == trusted_wsl
    assert result["executable_fingerprint"] == fingerprint
    assert result["argv"][:4] == [trusted_wsl, "--cd", str(tmp_path.resolve()), "--"]
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''    monkeypatch.setattr(server, "_wsl_executable", lambda: trusted_wsl)
    monkeypatch.setattr(server, "executable_identity_matches", lambda expected, actual: expected == actual)
    monkeypatch.setattr(
''',
    '''    fingerprint = "sha256:" + "f" * 64
    monkeypatch.setattr(server, "_wsl_executable", lambda: trusted_wsl)
    monkeypatch.setattr(server, "executable_fingerprint", lambda _path: fingerprint)
    monkeypatch.setattr(server, "executable_identity_guard", lambda expected, expected_fp, actual: __import__("contextlib").nullcontext(actual) if expected == actual and expected_fp == fingerprint else (_ for _ in ()).throw(ValueError("stale")))
    monkeypatch.setattr(
''',
)
replace_once(
    "tests/test_mcp_runtime.py",
    '''                "execute": True,
                "expected_executable": trusted_wsl,
''',
    '''                "execute": True,
                "expected_executable": trusted_wsl,
                "expected_executable_fingerprint": fingerprint,
''',
)

# Authority regression package mock.
replace_once(
    "tests/test_authority_regressions.py",
    '''    monkeypatch.setattr(server, "resolve_executable", lambda _name: trusted)
    monkeypatch.setattr(server, "executable_identity_matches", lambda expected, actual: expected == actual)
    monkeypatch.setattr(server, "invalidate_environment_cache", lambda _data_dir: False)
''',
    '''    fingerprint = "sha256:" + "1" * 64
    monkeypatch.setattr(server, "resolve_executable", lambda _name: trusted)
    monkeypatch.setattr(server, "executable_fingerprint", lambda _path: fingerprint)
    monkeypatch.setattr(server, "executable_identity_guard", lambda expected, expected_fp, actual: __import__("contextlib").nullcontext(actual) if expected == actual and expected_fp == fingerprint else (_ for _ in ()).throw(ValueError("stale")))
    monkeypatch.setattr(server, "invalidate_environment_cache", lambda _data_dir: False)
''',
)
replace_once(
    "tests/test_authority_regressions.py",
    '''                "expected_executable": trusted,
''',
    '''                "expected_executable": trusted,
                "expected_executable_fingerprint": fingerprint,
''',
)

# Docs and procedural surfaces.
replace_once(
    "README.md",
    '''→ plan-first execution echoes the reviewed executable as expected_executable
''',
    '''→ plan-first execution echoes reviewed executable path + SHA-256 identity
''',
)
replace_once(
    "README.md",
    '''`execute: false` produces a plan. For `capability_run`, `package_install`, and `sandbox_run`, that plan includes the resolved absolute `executable`. A later `execute: true` call must echo that exact value as `expected_executable`. If current resolution no longer identifies the same live absolute file, WDA returns `stale_plan` with `execution_started: false`; the caller must obtain a fresh plan rather than silently accepting the changed identity.

`expected_executable` is a stale-plan/identity precondition, **not** an approval token. The active host still decides whether the exact executing call is permitted. The runtime independently blocks forbidden capability classes and rejects malformed direct MCP calls even when a client skipped advertised JSON-schema validation.
''',
    '''`execute: false` produces a plan. For `capability_run`, `package_install`, and `sandbox_run`, that plan includes the resolved absolute `executable` and its `executable_fingerprint` (`sha256:...`). A later `execute: true` call must echo both unchanged as `expected_executable` and `expected_executable_fingerprint`. WDA re-resolves the executable and verifies both path and contents; changed path, changed same-path contents, missing identity, or an un-lockable file invalidates the plan before execution.

The expected executable path/fingerprint pair is a stale-plan/identity precondition, **not** an approval token. On Windows, WDA holds the verified executable open without write/delete sharing through process creation so the reviewed file cannot be replaced in the check-to-launch interval. The active host still decides whether the exact executing call is permitted. The runtime independently blocks forbidden capability classes and rejects malformed direct MCP calls even when a client skipped advertised JSON-schema validation.
''',
)
replace_once(
    "README.md",
    '''WDA resolves the selected package-manager executable into an absolute identity for the plan. `package_install(execute:false)` returns that `executable` with the exact argv for review. An executing call must pass it back as `expected_executable`; WDA re-resolves the current package-manager identity and refuses to launch with `stale_plan` if it no longer matches. After that check, the already-established current absolute path is the one passed to the bounded runner—there is no third PATH lookup at launch.
''',
    '''WDA resolves the selected package-manager executable into an absolute path and SHA-256 identity for the plan. `package_install(execute:false)` returns `executable` plus `executable_fingerprint` with the exact argv for review. Execution must pass both back unchanged. WDA re-resolves, re-hashes, and on Windows pins the verified file against write/delete replacement through cache invalidation and process creation; a path or content mismatch returns `stale_plan` before the installer starts.
''',
)
replace_once(
    "README.md",
    '''For plan-first execution, executable identity is checked before mutation/staging/launch. A stale reviewed plan is `not_executed`, not an execution failure.
''',
    '''For plan-first execution, reviewed executable path and content identity are checked before mutation/staging/launch and rechecked at the launch boundary where staging separates the two. A stale reviewed plan is `not_executed`, not an execution failure.
''',
)
replace_once(
    "README.md",
    '''`environment:auto` chooses the backend dictated by that property. If the caller names an explicit backend that does not satisfy the requirement, WDA rejects the request instead of silently weakening the boundary. `sandbox_run(execute:false)` also returns the absolute backend `executable`; execution must echo it as `expected_executable` so a changed backend identity invalidates the plan before staging or launch.
''',
    '''`environment:auto` chooses the backend dictated by that property. If the caller names an explicit backend that does not satisfy the requirement, WDA rejects the request instead of silently weakening the boundary. `sandbox_run(execute:false)` returns the backend `executable` and `executable_fingerprint`; execution must echo both so changed path or same-path backend contents invalidate the plan before staging or launch.
''',
)
replace_once(
    "SECURITY.md",
    '''For plan-first `capability_run`, `package_install`, and `sandbox_run`, the `execute:false` result exposes the absolute `executable` used to construct the reviewed plan. A later `execute:true` call must echo that value as `expected_executable`. WDA then establishes the current executable again and requires it to resolve to the same live absolute file before mutation, Sandbox staging, or process launch. A missing identity is invalid input; a changed identity returns `stale_plan` with `execution_started:false` and requires a fresh plan.

`expected_executable` is an identity/staleness precondition only. It is not evidence that a person approved anything and does not replace Claude/Codex permission authority.

After the identity check, the already-established current absolute path is carried into the process launch; WDA does not perform a later PATH lookup for that execution.
''',
    '''For plan-first `capability_run`, `package_install`, and `sandbox_run`, the `execute:false` result exposes both the resolved absolute `executable` and a SHA-256 `executable_fingerprint`. A later `execute:true` call must echo both as `expected_executable` and `expected_executable_fingerprint`. WDA re-establishes the current path and content identity before mutation, Sandbox staging, or process launch. Missing identity is invalid input; path changes, same-path content replacement, or inability to lock/verify the file return `stale_plan` with `execution_started:false` and require a fresh plan.

The expected executable path/fingerprint pair is an identity/staleness precondition only. It is not evidence that a person approved anything and does not replace Claude/Codex permission authority.

On Windows, the verified executable is held open without write/delete sharing through process creation, so a same-path replacement cannot occur between the final identity check and launch. Windows Sandbox identity is checked before staging and then reacquired/reverified for the actual launch. WDA does not perform a later PATH lookup for the executing file.
''',
)
replace_once(
    "skills/package-install/SKILL.md",
    '''2. Call `package_install` with `execute: false`. Present the returned source, exact package ID, `executable`, argv, command, and agreement flags as the planned mutation. The returned absolute `executable` is part of the reviewed execution identity.
3. When installation is actually requested, call the same tool with `execute: true` and set `expected_executable` to the exact `executable` returned by that reviewed plan. This is an identity precondition, not an approval token. If the runtime returns `stale_plan`, obtain a fresh `execute: false` plan instead of substituting the newly resolved path into the old plan. The active host's permission system decides whether the executing call proceeds.
''',
    '''2. Call `package_install` with `execute: false`. Present the returned source, exact package ID, `executable`, `executable_fingerprint`, argv, command, and agreement flags as the planned mutation. The absolute path plus SHA-256 fingerprint is the reviewed executable identity.
3. When installation is actually requested, call the same tool with `execute: true`, setting `expected_executable` and `expected_executable_fingerprint` to the exact values returned by that reviewed plan. These fields bind identity, not approval. If the runtime returns `stale_plan`, obtain a fresh `execute: false` plan instead of substituting a newly resolved path or fingerprint into the old plan. The active host's permission system decides whether the executing call proceeds.
''',
)
replace_once(
    "skills/package-install/SKILL.md",
    '''- Never change `expected_executable` merely to make an old plan executable; changed resolution invalidates the plan.
''',
    '''- Never change `expected_executable` or `expected_executable_fingerprint` merely to make an old plan executable; changed path or contents invalidate the plan.
''',
)
replace_once(
    "skills/sandbox-run/SKILL.md",
    '''5. Call `sandbox_run` with `execute: false` and inspect the selected route, requirement, payload list, absolute `executable`, and launch plan. Planning must not create the temporary bundle or launch the workload. Planning remains subject to the active host's native permission policy; do not assume a trusted hook will auto-allow Sandbox planning.
6. To launch, call the same reviewed tool with `execute: true` and set `expected_executable` to the exact `executable` returned by the plan. This binds execution to the reviewed backend identity; it is not approval. If the runtime returns `stale_plan`, obtain a fresh plan rather than replacing the expected path in-place. The active host decides whether the executing call proceeds; do not invent a second approval token.
''',
    '''5. Call `sandbox_run` with `execute: false` and inspect the selected route, requirement, payload list, absolute `executable`, `executable_fingerprint`, and launch plan. Planning must not create the temporary bundle or launch the workload. Planning remains subject to the active host's native permission policy; do not assume a trusted hook will auto-allow Sandbox planning.
6. To launch, call the same reviewed tool with `execute: true` and copy the plan's `executable` and `executable_fingerprint` unchanged into `expected_executable` and `expected_executable_fingerprint`. This binds execution to the reviewed backend file; it is not approval. If the runtime returns `stale_plan`, obtain a fresh plan rather than replacing the expected identity in-place. The active host decides whether the executing call proceeds; do not invent a second approval token.
''',
)
replace_once(
    "skills/sandbox-run/SKILL.md",
    '''- Never update `expected_executable` merely to make a stale plan run; re-plan instead.
''',
    '''- Never update the expected executable path or fingerprint merely to make a stale plan run; re-plan instead.
''',
)
replace_once(
    "skills/win-setup/SKILL.md",
    '''4. **For system packages, resolve identity before install.** Preserve an exact ID supplied by the user or authoritative project/config state. Otherwise call `package_search` and resolve the exact candidate. Then call `package_install` with `execute: false`, retain its absolute `executable`, and use that exact path as `expected_executable` when later requesting `execute: true`. A `stale_plan` result requires a fresh plan; do not silently accept a newly resolved package-manager identity. `expected_executable` binds identity only—the host remains the permission authority.
''',
    '''4. **For system packages, resolve identity before install.** Preserve an exact ID supplied by the user or authoritative project/config state. Otherwise call `package_search` and resolve the exact candidate. Then call `package_install` with `execute: false`, retain its `executable` and `executable_fingerprint`, and copy both unchanged into `expected_executable` and `expected_executable_fingerprint` when later requesting `execute: true`. A `stale_plan` result requires a fresh plan; do not silently accept a newly resolved or re-hashed package-manager identity. These fields bind identity only—the host remains the permission authority.
''',
)
replace_once(
    "skills/workflow-plan/SKILL.md",
    '''6. **Plan plan-first execution before requesting it.** For `capability_run`, `package_install`, or `sandbox_run`, call `execute: false` first and retain the returned absolute `executable` with the rest of the concrete plan. When issuing `execute: true`, pass that path unchanged as `expected_executable`. A `stale_plan` result means the executable identity changed or can no longer be established; obtain a fresh plan instead of silently accepting the new resolution. This identity binding is separate from permission.
''',
    '''6. **Plan plan-first execution before requesting it.** For `capability_run`, `package_install`, or `sandbox_run`, call `execute: false` first and retain both the returned absolute `executable` and `executable_fingerprint` with the concrete plan. When issuing `execute: true`, copy them unchanged into `expected_executable` and `expected_executable_fingerprint`. A `stale_plan` result means the executable path/content identity changed or can no longer be established; obtain a fresh plan instead of silently accepting the new identity. This binding is separate from permission.
''',
)
replace_once(
    "CHANGELOG.md",
    '''- Bind plan-first capability/package/Sandbox execution to the absolute executable identity returned by the reviewed plan; changed resolution returns `stale_plan` before execution, staging, or mutation and does not become a second approval token.
''',
    '''- Bind plan-first capability/package/Sandbox execution to the reviewed executable path plus SHA-256 content fingerprint; changed path or same-path contents return `stale_plan`, and Windows holds the verified file against write/delete replacement through process creation. This identity binding does not become a second approval token.
''',
)

print("Executable file-identity transforms applied")
