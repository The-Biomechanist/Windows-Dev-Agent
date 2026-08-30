"""
Environment discovery module for Windows Dev Agent Plugin.

Executes PowerShell discovery scripts and caches the results.
Provides methods to query environment state.
"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from ..models.environment import (
    EnvironmentSnapshot,
    SystemInfo,
    VirtualizationInfo,
    DevelopmentTools,
    Runtimes,
    RuntimeInfo,
    GitConfig,
    EditorAvailability,
    PowerShellModules,
    DevDrive,
)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
DISCOVERY_SCRIPT = Path(__file__).parent / "discovery.ps1"
CACHE_DIR = Path(os.environ.get("WINDOWS_DEV_AGENT_DATA_DIR", str(ROOT / ".cache"))).expanduser()
CACHE_FILE = CACHE_DIR / "environment.json"
CACHE_TTL_SECONDS = 300  # 5 minutes


class DiscoveryError(Exception):
    """Raised when discovery fails."""
    pass


class EnvironmentDiscovery:
    """Manages environment discovery via PowerShell scripts."""

    def __init__(self, cache_enabled: bool = True):
        self.cache_enabled = cache_enabled
        if self.cache_enabled:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def discover(self, force_refresh: bool = False) -> EnvironmentSnapshot:
        """
        Discover environment state.

        If cache is enabled and valid, returns cached result. Otherwise,
        executes discovery script and caches the result.

        Args:
            force_refresh: If True, skip cache and run discovery.

        Returns:
            EnvironmentSnapshot with discovered state.

        Raises:
            DiscoveryError: If discovery fails.
        """
        if self.cache_enabled and not force_refresh:
            cached = self._load_cache()
            if cached is not None:
                logger.info("Using cached environment snapshot")
                return cached

        logger.info("Running environment discovery...")
        try:
            snapshot = self._run_discovery()
            if self.cache_enabled:
                self._save_cache(snapshot)
            return snapshot
        except Exception as e:
            logger.error(f"Discovery failed: {e}")
            raise DiscoveryError(f"Environment discovery failed: {e}") from e

    def _run_discovery(self) -> EnvironmentSnapshot:
        """Execute the noninteractive PowerShell discovery script."""
        if not DISCOVERY_SCRIPT.exists():
            raise DiscoveryError(f"Discovery script not found: {DISCOVERY_SCRIPT}")

        try:
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(DISCOVERY_SCRIPT),
                ],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )

            if result.returncode != 0:
                logger.warning(f"PowerShell script returned {result.returncode}")
                if result.stderr:
                    logger.warning(f"stderr: {result.stderr}")

            try:
                data = json.loads(result.stdout)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse PowerShell output: {e}")
                return self._fallback_discovery()

            return self._parse_discovery_result(data)

        except subprocess.TimeoutExpired:
            raise DiscoveryError("Discovery script timed out")
        except FileNotFoundError:
            logger.warning("PowerShell not found, using fallback discovery")
            return self._fallback_discovery()
        except Exception as e:
            raise DiscoveryError(f"Failed to execute discovery: {e}") from e

    def _parse_discovery_result(self, data: dict) -> EnvironmentSnapshot:
        """Parse PowerShell discovery output into EnvironmentSnapshot."""
        try:
            timestamp = datetime.fromisoformat(data.get("timestamp", datetime.now().isoformat()))

            sys_data = data.get("system", {})
            system = SystemInfo(
                os_name=sys_data.get("os_name", "Unknown"),
                os_version=sys_data.get("os_version", ""),
                os_build=sys_data.get("os_build", ""),
                architecture=sys_data.get("architecture", ""),
                computer_name=sys_data.get("computer_name", ""),
                username=sys_data.get("username", ""),
                domain=sys_data.get("domain", ""),
                processor_count=sys_data.get("processor_count", 0),
                processor_name=sys_data.get("processor_name", ""),
                total_physical_memory_gb=sys_data.get("total_physical_memory_gb", 0.0),
            )

            virt_data = data.get("virtualization", {})
            dev_drives = [
                DevDrive(**drive) for drive in virt_data.get("dev_drives", [])
            ]
            virtualization = VirtualizationInfo(
                hyper_v_available=virt_data.get("hyper_v_available", False),
                wsl_installed=virt_data.get("wsl_installed", False),
                wsl_version=virt_data.get("wsl_version", ""),
                wsl_distros=virt_data.get("wsl_distros", []),
                windows_sandbox_available=virt_data.get("windows_sandbox_available", False),
                dev_drives=dev_drives,
            )

            tools_data = data.get("development_tools", {})
            development_tools = DevelopmentTools(
                winget_available=tools_data.get("winget_available", False),
                chocolatey_available=tools_data.get("chocolatey_available", False),
                scoop_available=tools_data.get("scoop_available", False),
                git_available=tools_data.get("git_available", False),
                docker_available=tools_data.get("docker_available", False),
                vscode_available=tools_data.get("vscode_available", False),
                visual_studio_available=tools_data.get("visual_studio_available", False),
            )

            rt_data = data.get("runtimes", {})
            runtimes = Runtimes(
                python=RuntimeInfo(
                    available=rt_data.get("python", {}).get("available", False),
                    version=rt_data.get("python", {}).get("version"),
                ),
                node=RuntimeInfo(
                    available=rt_data.get("node", {}).get("available", False),
                    version=rt_data.get("node", {}).get("version"),
                ),
                rust=RuntimeInfo(
                    available=rt_data.get("rust", {}).get("available", False),
                    version=rt_data.get("rust", {}).get("version"),
                ),
                golang=RuntimeInfo(
                    available=rt_data.get("golang", {}).get("available", False),
                    version=rt_data.get("golang", {}).get("version"),
                ),
                dotnet=RuntimeInfo(
                    available=rt_data.get("dotnet", {}).get("available", False),
                    versions=rt_data.get("dotnet", {}).get("versions", []),
                ),
            )

            git_data = data.get("git", {})
            git = GitConfig(
                available=git_data.get("available", False),
                version=git_data.get("version"),
                user_name=git_data.get("user_name"),
                user_email=git_data.get("user_email"),
            )

            editors_data = data.get("editors", {})
            editors = EditorAvailability(
                visual_studio_code=editors_data.get("visual_studio_code", False),
                visual_studio=editors_data.get("visual_studio", False),
                jetbrains_rider=editors_data.get("jetbrains_rider", False),
                jetbrains_pycharm=editors_data.get("jetbrains_pycharm", False),
                jetbrains_clion=editors_data.get("jetbrains_clion", False),
            )

            ps_data = data.get("powershell_modules", {})
            powershell_modules = PowerShellModules(
                count=ps_data.get("count", 0),
                modules=ps_data.get("modules", []),
            )

            return EnvironmentSnapshot(
                timestamp=timestamp,
                success=data.get("success", True),
                errors=data.get("errors", []),
                system=system,
                virtualization=virtualization,
                development_tools=development_tools,
                runtimes=runtimes,
                git=git,
                editors=editors,
                powershell_modules=powershell_modules,
            )

        except Exception as e:
            logger.error(f"Failed to parse discovery result: {e}")
            raise DiscoveryError(f"Failed to parse discovery result: {e}") from e

    def _fallback_discovery(self) -> EnvironmentSnapshot:
        """Fallback discovery for non-Windows systems."""
        logger.info("Using fallback discovery (non-Windows environment)")
        return EnvironmentSnapshot(
            timestamp=datetime.now(),
            success=False,
            errors=["PowerShell discovery unavailable (non-Windows system)"],
            system=SystemInfo(
                os_name=sys.platform,
                os_version="",
                os_build="",
                architecture="",
            ),
        )

    def _load_cache(self) -> Optional[EnvironmentSnapshot]:
        """Load cached environment snapshot if valid."""
        if not CACHE_FILE.exists():
            return None

        try:
            age = datetime.now() - datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
            if age > timedelta(seconds=CACHE_TTL_SECONDS):
                logger.debug(f"Cache expired ({age.total_seconds():.0f}s old)")
                return None

            with open(CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)

            return self._parse_discovery_result(data)

        except Exception as e:
            logger.warning(f"Failed to load cache: {e}")
            return None

    def _save_cache(self, snapshot: EnvironmentSnapshot):
        """Save environment snapshot to cache."""
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "timestamp": snapshot.timestamp.isoformat(),
                    "success": snapshot.success,
                    "errors": snapshot.errors,
                    "system": {
                        "os_name": snapshot.system.os_name,
                        "os_version": snapshot.system.os_version,
                        "os_build": snapshot.system.os_build,
                        "architecture": snapshot.system.architecture,
                        "computer_name": snapshot.system.computer_name,
                        "username": snapshot.system.username,
                        "domain": snapshot.system.domain,
                        "processor_count": snapshot.system.processor_count,
                        "processor_name": snapshot.system.processor_name,
                        "total_physical_memory_gb": snapshot.system.total_physical_memory_gb,
                    },
                    "virtualization": {
                        "hyper_v_available": snapshot.virtualization.hyper_v_available,
                        "wsl_installed": snapshot.virtualization.wsl_installed,
                        "wsl_version": snapshot.virtualization.wsl_version,
                        "wsl_distros": snapshot.virtualization.wsl_distros,
                        "windows_sandbox_available": snapshot.virtualization.windows_sandbox_available,
                        "dev_drives": [
                            {
                                "drive_letter": d.drive_letter,
                                "label": d.label,
                                "size_gb": d.size_gb,
                                "free_space_gb": d.free_space_gb,
                            }
                            for d in snapshot.virtualization.dev_drives
                        ],
                    },
                    "development_tools": {
                        "winget_available": snapshot.development_tools.winget_available,
                        "chocolatey_available": snapshot.development_tools.chocolatey_available,
                        "scoop_available": snapshot.development_tools.scoop_available,
                        "git_available": snapshot.development_tools.git_available,
                        "docker_available": snapshot.development_tools.docker_available,
                        "vscode_available": snapshot.development_tools.vscode_available,
                        "visual_studio_available": snapshot.development_tools.visual_studio_available,
                    },
                    "runtimes": {
                        "python": {
                            "available": snapshot.runtimes.python.available,
                            "version": snapshot.runtimes.python.version,
                        },
                        "node": {
                            "available": snapshot.runtimes.node.available,
                            "version": snapshot.runtimes.node.version,
                        },
                        "rust": {
                            "available": snapshot.runtimes.rust.available,
                            "version": snapshot.runtimes.rust.version,
                        },
                        "golang": {
                            "available": snapshot.runtimes.golang.available,
                            "version": snapshot.runtimes.golang.version,
                        },
                        "dotnet": {
                            "available": snapshot.runtimes.dotnet.available,
                            "versions": snapshot.runtimes.dotnet.versions,
                        },
                    },
                    "git": {
                        "available": snapshot.git.available,
                        "version": snapshot.git.version,
                        "user_name": snapshot.git.user_name,
                        "user_email": snapshot.git.user_email,
                    },
                    "editors": {
                        "visual_studio_code": snapshot.editors.visual_studio_code,
                        "visual_studio": snapshot.editors.visual_studio,
                        "jetbrains_rider": snapshot.editors.jetbrains_rider,
                        "jetbrains_pycharm": snapshot.editors.jetbrains_pycharm,
                        "jetbrains_clion": snapshot.editors.jetbrains_clion,
                    },
                    "powershell_modules": {
                        "count": snapshot.powershell_modules.count,
                        "modules": snapshot.powershell_modules.modules,
                    },
                }, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save cache: {e}")
