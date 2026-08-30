"""Windows environment discovery with truth-preserving cached snapshots."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Optional

from ..models.environment import EnvironmentSnapshot, SystemInfo

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DISCOVERY_SCRIPT = Path(__file__).parent / "discovery.ps1"
DEFAULT_CACHE_DIR = ROOT / ".cache"
CACHE_TTL_SECONDS = 300
DISCOVERY_TIMEOUT_SECONDS = 30


class DiscoveryError(Exception):
    """Raised when discovery cannot produce even a degraded snapshot."""


def _system_powershell() -> Optional[Path]:
    """Resolve the Windows-owned PowerShell executable without PATH lookup."""
    windows_root = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if not windows_root:
        return None
    candidate = (
        Path(windows_root)
        / "System32"
        / "WindowsPowerShell"
        / "v1.0"
        / "powershell.exe"
    )
    return candidate if candidate.is_file() else None


class EnvironmentDiscovery:
    def __init__(self, cache_enabled: bool = True, data_dir: Optional[Path] = None):
        self.cache_enabled = cache_enabled
        configured = data_dir or Path(
            os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR", str(DEFAULT_CACHE_DIR))
        ).expanduser()
        self.cache_dir = Path(configured).expanduser()
        self.cache_file = self.cache_dir / "environment.json"
        if self.cache_enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def discover(self, force_refresh: bool = False) -> EnvironmentSnapshot:
        if self.cache_enabled and not force_refresh:
            cached = self._load_cache()
            if cached is not None:
                return cached

        snapshot = self._run_discovery()
        if self.cache_enabled:
            self._save_cache(snapshot)
        return snapshot

    def _run_discovery(self) -> EnvironmentSnapshot:
        if not DISCOVERY_SCRIPT.exists():
            raise DiscoveryError(f"Discovery script not found: {DISCOVERY_SCRIPT}")

        powershell = _system_powershell()
        if powershell is None:
            return self._fallback_discovery(
                "System Windows PowerShell executable was not established"
            )

        try:
            result = subprocess.run(
                [
                    str(powershell),
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(DISCOVERY_SCRIPT),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=DISCOVERY_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DiscoveryError("Discovery script timed out") from exc
        except FileNotFoundError:
            return self._fallback_discovery("System Windows PowerShell disappeared before discovery")
        except OSError as exc:
            raise DiscoveryError(f"Failed to execute discovery: {exc}") from exc

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            detail = result.stderr.strip() or "PowerShell discovery returned invalid JSON"
            return self._fallback_discovery(detail)

        try:
            snapshot = EnvironmentSnapshot.from_dict(data)
        except Exception as exc:
            raise DiscoveryError(f"Failed to parse discovery result: {exc}") from exc

        if result.returncode != 0:
            snapshot.success = False
            snapshot.errors.append(
                f"Discovery process exited with code {result.returncode}: {result.stderr.strip()}"
            )
        return snapshot

    def _fallback_discovery(self, reason: str) -> EnvironmentSnapshot:
        return EnvironmentSnapshot(
            timestamp=datetime.now(),
            success=False,
            errors=[reason],
            system=SystemInfo(
                os_name=sys.platform,
                os_version="",
                os_build="",
                architecture="",
            ),
        )

    def _load_cache(self) -> Optional[EnvironmentSnapshot]:
        if not self.cache_file.exists():
            return None
        try:
            age = datetime.now() - datetime.fromtimestamp(self.cache_file.stat().st_mtime)
            if age > timedelta(seconds=CACHE_TTL_SECONDS):
                return None
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            return EnvironmentSnapshot.from_dict(data)
        except Exception as exc:
            logger.warning("Failed to load environment cache: %s", exc)
            return None

    def _save_cache(self, snapshot: EnvironmentSnapshot) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.cache_file.write_text(
                json.dumps(snapshot.to_dict(), indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to save environment cache: %s", exc)
