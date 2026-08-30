"""Windows environment discovery with truth-preserving cached snapshots."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import tempfile
import time
from typing import Iterator, Optional
import uuid

from src.execution import resolve_windows_system_executable, run_bounded
from ..models.environment import EnvironmentSnapshot, SystemInfo

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DISCOVERY_SCRIPT = Path(__file__).parent / "discovery.ps1"
DEFAULT_CACHE_DIR = ROOT / ".cache"
CACHE_TTL_SECONDS = 300
DISCOVERY_TIMEOUT_SECONDS = 30
MAX_CACHE_BYTES = 1024 * 1024
MAX_DISCOVERY_STDOUT_BYTES = 1024 * 1024
MAX_DISCOVERY_STDERR_BYTES = 64 * 1024
CACHE_NAME = "environment.json"
GENERATION_NAME = "environment.generation"
CACHE_LOCK_NAME = "environment.lock"
_CACHE_LOCK_TIMEOUT_SECONDS = 5.0


class DiscoveryError(Exception):
    """Raised internally when the native probe cannot produce a parsed snapshot."""


def _system_powershell() -> Optional[Path]:
    """Resolve the Windows-owned PowerShell executable without PATH lookup."""
    resolved = resolve_windows_system_executable(
        str(Path("WindowsPowerShell") / "v1.0" / "powershell.exe")
    )
    return Path(resolved) if resolved else None


def _atomic_text_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


@contextmanager
def _cache_lock(directory: Path) -> Iterator[None]:
    """Serialize cache admission, invalidation, and publication across Windows processes."""
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        yield
        return

    import msvcrt

    lock_path = directory / CACHE_LOCK_NAME
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + _CACHE_LOCK_TIMEOUT_SECONDS
        acquired = False
        while not acquired:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out acquiring environment cache lock")
                time.sleep(0.01)
        try:
            yield
        finally:
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass


def invalidate_environment_cache(data_dir: Path) -> bool:
    """Advance cache generation and remove any snapshot from the prior generation."""
    directory = Path(data_dir).expanduser()
    generation_file = directory / GENERATION_NAME
    cache_file = directory / CACHE_NAME
    try:
        with _cache_lock(directory):
            _atomic_text_write(generation_file, uuid.uuid4().hex)
            cache_file.unlink(missing_ok=True)
    except (OSError, TimeoutError):
        return False
    return True


class EnvironmentDiscovery:
    def __init__(self, cache_enabled: bool = True, data_dir: Optional[Path] = None):
        self.cache_enabled = cache_enabled
        configured = data_dir or Path(
            os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR", str(DEFAULT_CACHE_DIR))
        ).expanduser()
        self.cache_dir = Path(configured).expanduser()
        self.cache_file = self.cache_dir / CACHE_NAME
        self.generation_file = self.cache_dir / GENERATION_NAME

    def discover(self, force_refresh: bool = False) -> EnvironmentSnapshot:
        expected_generation = ""
        cache_usable = self.cache_enabled
        if self.cache_enabled:
            try:
                with _cache_lock(self.cache_dir):
                    if not force_refresh:
                        cached = self._load_cache()
                        if cached is not None:
                            return cached
                    expected_generation = self._generation()
            except (OSError, TimeoutError) as exc:
                logger.warning("Environment cache unavailable for this discovery: %s", exc)
                cache_usable = False

        try:
            snapshot = self._run_discovery()
        except DiscoveryError as exc:
            snapshot = self._fallback_discovery(str(exc))

        if cache_usable:
            try:
                with _cache_lock(self.cache_dir):
                    self._save_cache(snapshot, expected_generation=expected_generation)
            except (OSError, TimeoutError) as exc:
                logger.warning("Environment cache unavailable for snapshot publication: %s", exc)
        return snapshot

    def _run_discovery(self) -> EnvironmentSnapshot:
        if not DISCOVERY_SCRIPT.exists():
            raise DiscoveryError(f"Discovery script not found: {DISCOVERY_SCRIPT}")

        powershell = _system_powershell()
        if powershell is None:
            return self._fallback_discovery(
                "System Windows PowerShell executable was not established"
            )

        result = run_bounded(
            [
                str(powershell),
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(DISCOVERY_SCRIPT),
            ],
            timeout=DISCOVERY_TIMEOUT_SECONDS,
            stdout_bytes=MAX_DISCOVERY_STDOUT_BYTES,
            stderr_bytes=MAX_DISCOVERY_STDERR_BYTES,
        )
        if result.get("timed_out") is True:
            raise DiscoveryError("Discovery script timed out")
        if result.get("execution_started") is False:
            return self._fallback_discovery(
                f"System Windows PowerShell could not start: {result.get('error', 'unknown launch error')}"
            )

        stdout = str(result.get("stdout", ""))
        stderr = str(result.get("stderr", ""))
        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            detail = stderr.strip() or "PowerShell discovery returned invalid or oversized JSON"
            return self._fallback_discovery(detail)

        try:
            snapshot = EnvironmentSnapshot.from_dict(data)
        except Exception as exc:
            raise DiscoveryError(f"Failed to parse discovery result: {exc}") from exc

        returncode = result.get("returncode")
        if returncode != 0:
            snapshot.success = False
            snapshot.errors.append(
                f"Discovery process exited with code {returncode}: {stderr.strip()}"
            )
        return snapshot

    def _fallback_discovery(self, reason: str) -> EnvironmentSnapshot:
        return EnvironmentSnapshot(
            timestamp=datetime.now(),
            success=False,
            errors=[reason],
            system=SystemInfo(
                os_name=os.name,
                os_version="",
                os_build="",
                architecture="",
            ),
        )

    def _generation(self) -> str:
        try:
            raw = self.generation_file.read_bytes()
        except FileNotFoundError:
            return ""
        except OSError:
            return "unknown"
        if len(raw) > 256:
            return "invalid"
        return raw.decode("ascii", errors="replace")

    def _load_cache(self) -> Optional[EnvironmentSnapshot]:
        if not self.cache_file.exists():
            return None
        try:
            stat = self.cache_file.stat()
            if stat.st_size > MAX_CACHE_BYTES:
                return None
            age = datetime.now() - datetime.fromtimestamp(stat.st_mtime)
            if age > timedelta(seconds=CACHE_TTL_SECONDS):
                return None
            raw = self.cache_file.read_bytes()
            if len(raw) > MAX_CACHE_BYTES:
                return None
            data = json.loads(raw.decode("utf-8"))
            return EnvironmentSnapshot.from_dict(data)
        except Exception as exc:
            logger.warning("Failed to load environment cache: %s", exc)
            return None

    def _save_cache(self, snapshot: EnvironmentSnapshot, *, expected_generation: str) -> None:
        try:
            if self._generation() != expected_generation:
                return
            payload = json.dumps(snapshot.to_dict(), separators=(",", ":"))
            if len(payload.encode("utf-8")) > MAX_CACHE_BYTES:
                logger.warning("Environment snapshot exceeds cache size limit")
                return
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=self.cache_file.name + ".",
                suffix=".tmp",
                dir=str(self.cache_dir),
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                if self._generation() != expected_generation:
                    temp_path.unlink(missing_ok=True)
                    return
                os.replace(temp_path, self.cache_file)
                if self._generation() != expected_generation:
                    self.cache_file.unlink(missing_ok=True)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise
        except Exception as exc:
            logger.warning("Failed to save environment cache: %s", exc)
