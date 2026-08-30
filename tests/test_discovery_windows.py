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


def test_wsl_enabled_but_missing_executable_is_not_swallowed_as_plain_missing():
    text = SCRIPT.read_text(encoding="utf-8")
    inconsistent = 'elseif ($wslFeature.available -eq $true -and $wslExePresent -eq $false)'
    generic_missing = 'elseif ($wslFeature.available -eq $false -or $wslExePresent -eq $false)'
    assert inconsistent in text and generic_missing in text
    assert text.index(inconsistent) < text.index(generic_missing)
    block = text[text.index(inconsistent):text.index(generic_missing)]
    assert 'Add-DiscoveryError "WSL feature is enabled but wsl.exe was not found"' in block
    assert '$wslInstalled = $null' in block


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
    assert payload["virtualization"]["hyper_v_state"]
    assert payload["virtualization"]["windows_sandbox_state"]
    assert "username" not in payload["system"]
    assert "domain" not in payload["system"]
    assert "user_name" not in payload["git"]
    assert "user_email" not in payload["git"]
    assert "powershell_modules" not in payload


def test_native_environment_discovery_round_trips_its_own_output(tmp_path: Path):
    snapshot = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path).discover(force_refresh=True)
    cached = json.loads((tmp_path / "environment.json").read_text(encoding="utf-8"))
    assert cached == snapshot.to_dict()
    assert cached["probe_states"]["windows_sandbox"] in {"available", "missing", "unknown"}
    assert cached["probe_states"]["wsl"] in {"available", "missing", "unknown"}
