"""Visual Studio presence must come from executable or installer authority, not directory names."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "src" / "discovery" / "discovery.ps1"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Visual Studio discovery")


def _system_powershell() -> Path:
    return Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def test_visual_studio_presence_uses_installer_locator_not_directory_name():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Get-VisualStudioAvailability" in text
    assert "Microsoft Visual Studio\", \"Installer\", \"vswhere.exe" in text
    assert "-prerelease -latest -property installationPath" in text
    assert 'Test-Path "C:\\Program Files\\Microsoft Visual Studio"' not in text
    assert "visual_studio_available = $visualStudioAvailable" in text
    assert "visual_studio = $visualStudioAvailable" in text


def test_visual_studio_locator_establishes_hosted_runner_instance():
    command = rf'''
$ErrorActionPreference = 'Stop'
$null = . '{SCRIPT}'
$result = Get-VisualStudioAvailability
if ($result -ne $true) {{ throw "Visual Studio locator did not establish hosted instance: $result" }}
'''
    result = subprocess.run(
        [
            str(_system_powershell()),
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command,
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
