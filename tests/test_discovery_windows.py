"""Native Windows discovery probes.

These tests execute the shipped Windows producer and composed discovery path on
Windows CI. They permit probes to remain unknown when the operating system does
not expose or permit the relevant authority surface; unknown is not rewritten as
missing.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.discovery.discovery import EnvironmentDiscovery

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "src" / "discovery" / "discovery.ps1"

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows probe")


def _system_windows_powershell() -> Path:
    return Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def test_shipped_optional_feature_identities_match_windows_contract():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'Get-OptionalFeatureProbe "Microsoft-Hyper-V"' in text
    assert 'Get-OptionalFeatureProbe "Containers-DisposableClientVM"' in text
    assert 'Get-OptionalFeatureProbe "Containers-DisposableVM"' not in text
    assert 'Get-OptionalFeatureProbe "Microsoft-Windows-Subsystem-Linux"' not in text
    assert "Get-WindowsOptionalFeature -Online" in text


def test_powershell_producer_does_not_infer_wsl_or_dev_drive_identity():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'FileSystemLabel -match "DevDrive"' not in text
    assert "$wslInstalled" not in text
    assert "wslExePresent" not in text
    assert "wsl_installed = $null" in text
    assert "dev_drive_enabled = $null" in text
    assert "dev_drives = $null" in text


def test_native_discovery_script_emits_truth_preserving_json():
    powershell = _system_windows_powershell()
    assert powershell.is_file()
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT)],
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
    # These facts are intentionally left unknown by the broad producer and are
    # overlaid from wsl.exe/direct Win32 by EnvironmentDiscovery.
    assert payload["virtualization"]["wsl_installed"] is None
    assert payload["virtualization"]["dev_drive_enabled"] is None
    assert payload["virtualization"]["dev_drives"] is None
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
    assert cached["probe_states"]["dev_drive"] in {"available", "missing", "unknown"}
    assert cached["virtualization"]["dev_drives"] is None or isinstance(cached["virtualization"]["dev_drives"], list)
