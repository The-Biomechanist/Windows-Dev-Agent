"""Windows discovery must not execute ambient PowerShell modules while probing state."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "src" / "discovery" / "discovery.ps1"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell authority contract")


def _system_powershell() -> Path:
    return Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def test_discovery_never_loads_ambient_powershell_modules(tmp_path: Path):
    """Ambient PSModulePath must not become executable authority for discovery."""
    marker = tmp_path / "ambient-module-loaded.txt"
    modules = tmp_path / "modules"
    module = modules / "WdaDiscoveryCanary"
    module.mkdir(parents=True)

    exported = [
        "Get-Date",
        "ConvertTo-Json",
        "Select-Object",
        "Where-Object",
        "ForEach-Object",
        "Test-Path",
        "Get-Item",
        "Get-ItemProperty",
        "Get-ChildItem",
        "Add-Type",
        "Get-CimInstance",
        "Get-WindowsOptionalFeature",
        "Get-Volume",
        "Get-AppxPackage",
        "winget",
        "choco",
        "scoop",
        "git",
        "docker",
        "code",
        "devenv",
        "python",
        "node",
        "rustc",
        "go",
        "dotnet",
    ]
    escaped_marker = str(marker).replace("'", "''")
    functions = "\n".join(f"function {name} {{ return $null }}" for name in exported)
    (module / "WdaDiscoveryCanary.psm1").write_text(
        f"[IO.File]::WriteAllText('{escaped_marker}', 'loaded')\n{functions}\n",
        encoding="utf-8",
    )
    quoted_exports = ", ".join(f"'{name}'" for name in exported)
    (module / "WdaDiscoveryCanary.psd1").write_text(
        "@{\n"
        "  RootModule = 'WdaDiscoveryCanary.psm1'\n"
        "  ModuleVersion = '1.0.0'\n"
        f"  FunctionsToExport = @({quoted_exports})\n"
        "}\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PSModulePath"] = str(modules) + os.pathsep + env.get("PSModulePath", "")
    result = subprocess.run(
        [
            str(_system_powershell()),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )

    assert result.stdout.strip(), result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload["success"], bool)
    assert not marker.exists(), "discovery imported and executed an ambient PowerShell module"
