"""Canonical environment state for Windows Dev Agent.

Availability fields are tri-state: ``True`` means observed available/enabled,
``False`` means observed missing/disabled, and ``None`` means the probe did not
establish the fact. That distinction is preserved through cache serialization
and model-facing output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
import re
from typing import Any, Dict, List, Optional


def availability_state(value: Optional[bool]) -> str:
    if value is True:
        return "available"
    if value is False:
        return "missing"
    return "unknown"


def parse_timestamp(value: Any) -> datetime:
    """Parse ISO/Round-trip timestamps across the supported Python range.

    Windows PowerShell's round-trip ``o`` format commonly emits seven
    fractional-second digits. Python 3.9's ``datetime.fromisoformat`` accepts
    microsecond precision, so trim only excess fractional precision while
    preserving the timezone offset. ``Z`` is normalized for older Python.
    """
    raw = str(value or datetime.now().isoformat()).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    raw = re.sub(r"(\.\d{6})\d+(?=(?:[+-]\d{2}:\d{2})?$)", r"\1", raw)
    return datetime.fromisoformat(raw)


@dataclass
class SystemInfo:
    os_name: str = "Unknown"
    os_version: str = ""
    os_build: str = ""
    architecture: str = ""
    processor_count: int = 0
    processor_name: str = ""
    total_physical_memory_gb: float = 0.0

    def is_windows_11(self) -> bool:
        if "11" in self.os_name:
            return True
        try:
            return int(self.os_build) >= 22000
        except (TypeError, ValueError):
            return False

    def is_windows_10(self) -> bool:
        return "10" in self.os_name


@dataclass
class DevDrive:
    drive_letter: str
    label: str
    size_gb: float
    free_space_gb: float

    @property
    def usage_percent(self) -> float:
        if self.size_gb == 0:
            return 0.0
        return ((self.size_gb - self.free_space_gb) / self.size_gb) * 100


@dataclass
class VirtualizationInfo:
    hyper_v_available: Optional[bool] = None
    hyper_v_state: str = "unknown"
    wsl_installed: Optional[bool] = None
    wsl_version: Optional[str] = None
    wsl_distros: List[str] = field(default_factory=list)
    windows_sandbox_available: Optional[bool] = None
    windows_sandbox_state: str = "unknown"
    dev_drive_enabled: Optional[bool] = None
    dev_drive_state: str = "unknown"
    dev_drives: Optional[List[DevDrive]] = None

    def has_wsl(self) -> bool:
        return self.wsl_installed is True

    def has_hyper_v(self) -> bool:
        return self.hyper_v_available is True

    def has_sandbox(self) -> bool:
        return self.windows_sandbox_available is True

    def get_available_isolation_options(self) -> List[str]:
        options: List[str] = []
        if self.has_hyper_v():
            options.append("hyper-v")
        if self.has_wsl():
            options.append("wsl")
        if self.has_sandbox():
            options.append("windows-sandbox")
        return options


@dataclass
class DevelopmentTools:
    winget_available: Optional[bool] = None
    chocolatey_available: Optional[bool] = None
    scoop_available: Optional[bool] = None
    git_available: Optional[bool] = None
    docker_available: Optional[bool] = None
    vscode_available: Optional[bool] = None
    visual_studio_available: Optional[bool] = None

    def get_available_package_managers(self) -> List[str]:
        managers: List[str] = []
        if self.winget_available is True:
            managers.append("winget")
        if self.chocolatey_available is True:
            managers.append("chocolatey")
        if self.scoop_available is True:
            managers.append("scoop")
        return managers


@dataclass
class RuntimeInfo:
    available: Optional[bool] = None
    version: Optional[str] = None
    versions: List[str] = field(default_factory=list)


@dataclass
class Runtimes:
    python: RuntimeInfo = field(default_factory=RuntimeInfo)
    node: RuntimeInfo = field(default_factory=RuntimeInfo)
    rust: RuntimeInfo = field(default_factory=RuntimeInfo)
    golang: RuntimeInfo = field(default_factory=RuntimeInfo)
    dotnet: RuntimeInfo = field(default_factory=RuntimeInfo)

    def get_available_runtimes(self) -> List[str]:
        return [
            name
            for name, runtime in (
                ("python", self.python),
                ("node", self.node),
                ("rust", self.rust),
                ("golang", self.golang),
                ("dotnet", self.dotnet),
            )
            if runtime.available is True
        ]


@dataclass
class GitConfig:
    available: Optional[bool] = None
    version: Optional[str] = None


@dataclass
class EditorAvailability:
    visual_studio_code: Optional[bool] = None
    visual_studio: Optional[bool] = None
    jetbrains_rider: Optional[bool] = None
    jetbrains_pycharm: Optional[bool] = None
    jetbrains_clion: Optional[bool] = None

    def get_available_editors(self) -> List[str]:
        return [
            name
            for name, value in (
                ("vscode", self.visual_studio_code),
                ("visual-studio", self.visual_studio),
                ("rider", self.jetbrains_rider),
                ("pycharm", self.jetbrains_pycharm),
                ("clion", self.jetbrains_clion),
            )
            if value is True
        ]


@dataclass
class EnvironmentSnapshot:
    timestamp: datetime
    success: bool
    errors: List[str] = field(default_factory=list)
    system: SystemInfo = field(default_factory=SystemInfo)
    virtualization: VirtualizationInfo = field(default_factory=VirtualizationInfo)
    development_tools: DevelopmentTools = field(default_factory=DevelopmentTools)
    runtimes: Runtimes = field(default_factory=Runtimes)
    git: GitConfig = field(default_factory=GitConfig)
    editors: EditorAvailability = field(default_factory=EditorAvailability)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "success": self.success,
            "errors": list(self.errors),
            "system": {
                "os_name": self.system.os_name,
                "os_version": self.system.os_version,
                "os_build": self.system.os_build,
                "architecture": self.system.architecture,
                "processor_count": self.system.processor_count,
                "processor_name": self.system.processor_name,
                "total_physical_memory_gb": self.system.total_physical_memory_gb,
            },
            "virtualization": {
                "hyper_v_available": self.virtualization.hyper_v_available,
                "hyper_v_state": self.virtualization.hyper_v_state,
                "wsl_installed": self.virtualization.wsl_installed,
                "wsl_version": self.virtualization.wsl_version,
                "wsl_distros": list(self.virtualization.wsl_distros),
                "windows_sandbox_available": self.virtualization.windows_sandbox_available,
                "windows_sandbox_state": self.virtualization.windows_sandbox_state,
                "dev_drive_enabled": self.virtualization.dev_drive_enabled,
                "dev_drive_state": self.virtualization.dev_drive_state,
                "dev_drives": None if self.virtualization.dev_drives is None else [
                    {
                        "drive_letter": drive.drive_letter,
                        "label": drive.label,
                        "size_gb": drive.size_gb,
                        "free_space_gb": drive.free_space_gb,
                    }
                    for drive in self.virtualization.dev_drives
                ],
            },
            "development_tools": {
                "winget_available": self.development_tools.winget_available,
                "chocolatey_available": self.development_tools.chocolatey_available,
                "scoop_available": self.development_tools.scoop_available,
                "git_available": self.development_tools.git_available,
                "docker_available": self.development_tools.docker_available,
                "vscode_available": self.development_tools.vscode_available,
                "visual_studio_available": self.development_tools.visual_studio_available,
            },
            "runtimes": {
                "python": vars(self.runtimes.python),
                "node": vars(self.runtimes.node),
                "rust": vars(self.runtimes.rust),
                "golang": vars(self.runtimes.golang),
                "dotnet": vars(self.runtimes.dotnet),
            },
            "git": vars(self.git),
            "editors": vars(self.editors),
            "probe_states": {
                "hyper_v": availability_state(self.virtualization.hyper_v_available),
                "wsl": availability_state(self.virtualization.wsl_installed),
                "windows_sandbox": availability_state(self.virtualization.windows_sandbox_available),
                "dev_drive": availability_state(self.virtualization.dev_drive_enabled),
                "winget": availability_state(self.development_tools.winget_available),
                "git": availability_state(self.development_tools.git_available),
            },
            "available_package_managers": self.development_tools.get_available_package_managers(),
            "available_runtimes": self.runtimes.get_available_runtimes(),
            "available_editors": self.editors.get_available_editors(),
            "available_isolation_options": self.virtualization.get_available_isolation_options(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EnvironmentSnapshot":
        system = SystemInfo(**dict(data.get("system", {})))
        virt_data = dict(data.get("virtualization", {}))
        raw_dev_drives = virt_data.pop("dev_drives", None)
        dev_drives = None if raw_dev_drives is None else [DevDrive(**drive) for drive in raw_dev_drives]
        virtualization = VirtualizationInfo(**virt_data, dev_drives=dev_drives)
        development_tools = DevelopmentTools(**dict(data.get("development_tools", {})))
        runtimes_data = data.get("runtimes", {})
        runtimes = Runtimes(
            python=RuntimeInfo(**dict(runtimes_data.get("python", {}))),
            node=RuntimeInfo(**dict(runtimes_data.get("node", {}))),
            rust=RuntimeInfo(**dict(runtimes_data.get("rust", {}))),
            golang=RuntimeInfo(**dict(runtimes_data.get("golang", {}))),
            dotnet=RuntimeInfo(**dict(runtimes_data.get("dotnet", {}))),
        )
        git = GitConfig(**dict(data.get("git", {})))
        editors = EditorAvailability(**dict(data.get("editors", {})))
        return cls(
            timestamp=parse_timestamp(data.get("timestamp")),
            success=bool(data.get("success", False)),
            errors=list(data.get("errors", [])),
            system=system,
            virtualization=virtualization,
            development_tools=development_tools,
            runtimes=runtimes,
            git=git,
            editors=editors,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "EnvironmentSnapshot":
        return cls.from_dict(json.loads(json_str))
