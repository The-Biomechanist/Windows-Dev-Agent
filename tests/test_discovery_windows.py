"""Native Windows discovery probes.

These tests execute the shipped PowerShell producer on Windows CI instead of
feeding idealized JSON into the Python parser. They intentionally permit
feature-state probes to return unknown when the runner lacks sufficient
privilege; the contract is valid JSON plus preserved unknown/error state.
"""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.discovery.discovery import EnvironmentDiscovery

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "src" / "discovery" / "discovery.ps1"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows probe")


def test_shipped_optional_feature_identities_match_windows_contract():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'Get-OptionalFeatureProbe "Microsoft-Hyper-V"' in text
    assert 'Get-OptionalFeatureProbe "Containers-DisposableClientVM"' in text
    assert 'Get-OptionalFeatureProbe "Containers-DisposableVM"' not in text
    assert 'Get-OptionalFeatureProbe "Microsoft-Windows-Subsystem-Linux"' in text
    assert "Get-WindowsOptionalFeature -Online" in text


def test_wsl_discovery_is_store_inbox_agnostic_and_default_distro_aware():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'MicrosoftCorporationII.WindowsSubsystemForLinux' in text
    assert 'Services\\WslService' in text
    assert 'Services\\LxssManager' in text
    assert 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss' in text
    assert 'Get-WslPolicyValue "AllowWSL"' in text
    assert 'Get-WslPolicyValue "AllowInboxWSL"' in text
    assert 'Get-WslPolicyValue "AllowWSL1"' in text
    assert 'Services\\WslService' in text
    assert 'Services\\LxssManager' in text
    assert 'wsl_available = $wslAvailable' in text
    assert 'wsl_default_distro = $wslDefaultDistro' in text


def test_dev_drive_discovery_uses_native_persistent_volume_state_not_label():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'FSCTL_QUERY_PERSISTENT_VOLUME_STATE' in text
    assert 'PERSISTENT_VOLUME_STATE_DEV_VOLUME' in text
    assert 'TryIsDeveloperVolume' in text
    assert 'FileSystemLabel -match "DevDrive"' not in text


def test_native_discovery_script_emits_truth_preserving_json():
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.stdout.strip(), result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload["success"], bool)
    assert isinstance(payload["errors"], list)
    assert payload["virtualization"]["hyper_v_available"] in {True, False, None}
    assert payload["virtualization"]["windows_sandbox_available"] in {True, False, None}
    assert payload["virtualization"]["wsl_installed"] in {True, False, None}
    assert payload["virtualization"]["wsl_available"] in {True, False, None}
    assert payload["virtualization"]["dev_drive_inventory_state"] in {"available", "unknown"}
    assert payload["virtualization"]["hyper_v_state"]
    assert payload["virtualization"]["windows_sandbox_state"]
    assert "username" not in payload["system"]
    assert "domain" not in payload["system"]
    assert "user_name" not in payload["git"]
    assert "user_email" not in payload["git"]
    assert "powershell_modules" not in payload




def test_broad_tool_presence_uses_native_application_resolution_with_wda_path_policy():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Get-WdaApplicationSearchPath" in text
    assert "Get-Command -Name $Name -CommandType Application" in text
    assert "[Environment]::CurrentDirectory" in text
    assert "[IO.Path]::IsPathRooted" in text
    assert "$env:PATH = [string]$script:WdaApplicationSearchPath.path" in text
    assert "$env:PATH = $originalPath" in text


def test_broad_tool_presence_rejects_alias_function_and_process_cwd_application(tmp_path: Path):
    fake = tmp_path / "wda-fake.exe"
    fake.write_bytes(b"not-an-executable")
    original_path = __import__("os").environ.get("PATH", "")
    command = rf'''
$ErrorActionPreference = 'Stop'
$env:PATH = '.;{tmp_path};' + $env:PATH
$null = . '{SCRIPT}'
function wda-function-only {{ 'function' }}
Set-Alias -Name wda-alias-only -Value Get-Date
if (Test-CommandAvailable 'wda-function-only') {{ throw 'function counted as application' }}
if (Test-CommandAvailable 'wda-alias-only') {{ throw 'alias counted as application' }}
if (Test-CommandAvailable 'wda-fake') {{ throw 'process cwd application was trusted' }}
if (-not (Test-CommandAvailable 'python')) {{ throw 'real PATH application was lost' }}
'''
    env = dict(__import__("os").environ)
    env["PATH"] = ".;" + str(tmp_path) + ";" + original_path
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=tmp_path,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_native_environment_discovery_round_trips_its_own_output(tmp_path: Path):
    snapshot = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path).discover(force_refresh=True)
    cached = json.loads((tmp_path / "environment.json").read_text(encoding="utf-8"))
    assert cached == snapshot.to_dict()
    assert cached["probe_states"]["windows_sandbox"] in {"available", "missing", "unknown"}
    assert cached["probe_states"]["wsl"] in {"available", "missing", "unknown"}
