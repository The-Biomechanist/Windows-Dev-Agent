"""Discovery WSL availability must compose the same implementation authority as routing."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "src" / "discovery" / "discovery.ps1"
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows WSL authority contract")


def _system_powershell() -> Path:
    return Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def test_wsl_route_implementation_matrix_matches_service_authority():
    command = rf'''
$ErrorActionPreference = 'Stop'
$null = . '{SCRIPT}'
function Format-TriState($Value) {{
    if ($null -eq $Value) {{ return 'unknown' }}
    if ($Value -eq $true) {{ return 'true' }}
    return 'false'
}}
$cases = @(
    (Format-TriState (Get-WslRouteImplementationAllowed -StoreServicePresent $true  -InboxServicePresent $false -InboxPolicyAllowed $false)),
    (Format-TriState (Get-WslRouteImplementationAllowed -StoreServicePresent $false -InboxServicePresent $true  -InboxPolicyAllowed $false)),
    (Format-TriState (Get-WslRouteImplementationAllowed -StoreServicePresent $false -InboxServicePresent $true  -InboxPolicyAllowed $true)),
    (Format-TriState (Get-WslRouteImplementationAllowed -StoreServicePresent $false -InboxServicePresent $false -InboxPolicyAllowed $true)),
    (Format-TriState (Get-WslRouteImplementationAllowed -StoreServicePresent $null  -InboxServicePresent $true  -InboxPolicyAllowed $true)),
    (Format-TriState (Get-WslRouteImplementationAllowed -StoreServicePresent $false -InboxServicePresent $null  -InboxPolicyAllowed $true))
)
$cases | ConvertTo-Json -Compress
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
    assert json.loads(result.stdout.strip()) == [
        "true",
        "false",
        "true",
        "false",
        "unknown",
        "unknown",
    ]


def test_store_package_is_not_used_as_route_implementation_authority():
    text = SCRIPT.read_text(encoding="utf-8")
    helper_start = text.index("function Get-WslRouteImplementationAllowed")
    helper_end = text.index("function Initialize-DevDriveNativeProbe", helper_start)
    helper = text[helper_start:helper_end]
    assert "$StoreServicePresent" in helper
    assert "$InboxServicePresent" in helper
    assert "$InboxPolicyAllowed" in helper
    assert "storeWslPresent" not in helper
    assert "storeWslImplementationPresent" not in helper
