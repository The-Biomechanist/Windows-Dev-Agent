"""Contract tests for truth-preserving Windows discovery and serialization."""

import json
from datetime import datetime
from pathlib import Path

from src.discovery import discovery as discovery_module
from src.discovery.discovery import EnvironmentDiscovery, invalidate_environment_cache
from src.models.environment import (
    DevDrive,
    DevelopmentTools,
    EditorAvailability,
    EnvironmentSnapshot,
    GitConfig,
    RuntimeInfo,
    Runtimes,
    SystemInfo,
    VirtualizationInfo,
    availability_state,
)


MOCK_DISCOVERY_OUTPUT = {
    "timestamp": datetime.now().isoformat(),
    "success": False,
    "errors": ["Optional feature 'Microsoft-Hyper-V' was not established"],
    "system": {
        "os_name": "Microsoft Windows 11 Pro",
        "os_version": "10.0.22631",
        "os_build": "22631",
        "architecture": "64-bit",
        "processor_count": 8,
        "processor_name": "Test CPU",
        "total_physical_memory_gb": 32.0,
    },
    "virtualization": {
        "hyper_v_available": None,
        "hyper_v_state": "unknown",
        "wsl_installed": True,
        "wsl_version": "WSL version: 2.5.9.0",
        "wsl_distros": ["Ubuntu"],
        "windows_sandbox_available": False,
        "windows_sandbox_state": "Disabled",
        "dev_drives": [{"drive_letter": "D", "label": "DevDrive", "size_gb": 100.0, "free_space_gb": 80.0}],
    },
    "development_tools": {
        "winget_available": True,
        "chocolatey_available": False,
        "scoop_available": None,
        "git_available": True,
        "docker_available": False,
        "vscode_available": True,
        "visual_studio_available": False,
    },
    "runtimes": {
        "python": {"available": True, "version": "Python 3.11.9", "versions": []},
        "node": {"available": False, "version": None, "versions": []},
        "rust": {"available": None, "version": None, "versions": []},
        "golang": {"available": False, "version": None, "versions": []},
        "dotnet": {"available": True, "version": None, "versions": ["8.0.100"]},
    },
    "git": {"available": True, "version": "git version 2.45.0"},
    "editors": {
        "visual_studio_code": True,
        "visual_studio": False,
        "jetbrains_rider": None,
        "jetbrains_pycharm": False,
        "jetbrains_clion": False,
    },
}


def _result(*, returncode=0, stdout=None, stderr="", timed_out=False, started=True):
    value = {
        "succeeded": returncode == 0 and not timed_out,
        "returncode": returncode,
        "stdout": json.dumps(MOCK_DISCOVERY_OUTPUT) if stdout is None else stdout,
        "stderr": stderr,
        "execution_started": started,
    }
    if timed_out:
        value["timed_out"] = True
    return value


def _patch_native_probe(monkeypatch, result):
    monkeypatch.setattr(discovery_module, "_system_powershell", lambda: Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
    monkeypatch.setattr(discovery_module, "run_bounded", lambda *_args, **_kwargs: result)


def test_availability_state_is_tri_state():
    assert availability_state(True) == "available"
    assert availability_state(False) == "missing"
    assert availability_state(None) == "unknown"


def test_unknown_optional_feature_is_not_missing():
    virt = VirtualizationInfo(hyper_v_available=None, windows_sandbox_available=False)
    assert virt.has_hyper_v() is False
    assert availability_state(virt.hyper_v_available) == "unknown"
    assert availability_state(virt.windows_sandbox_available) == "missing"


def test_dev_drive_is_not_reported_as_isolation_backend():
    virt = VirtualizationInfo(dev_drives=[DevDrive("D", "DevDrive", 100.0, 80.0)])
    assert "dev-drive" not in virt.get_available_isolation_options()


def test_windows_version_detection_handles_unknown_build():
    assert SystemInfo(os_name="Unknown", os_build="").is_windows_11() is False


def test_snapshot_roundtrip_is_lossless_for_canonical_fields():
    snapshot = EnvironmentSnapshot(
        timestamp=datetime.now(),
        success=False,
        errors=["probe unknown"],
        system=SystemInfo(os_name="Windows 11", os_build="22631"),
        virtualization=VirtualizationInfo(
            hyper_v_available=None,
            hyper_v_state="unknown",
            wsl_installed=True,
            windows_sandbox_available=False,
            windows_sandbox_state="Disabled",
        ),
        development_tools=DevelopmentTools(winget_available=True, scoop_available=None),
        runtimes=Runtimes(python=RuntimeInfo(available=True, version="3.11")),
        git=GitConfig(available=True, version="git version 2.45"),
        editors=EditorAvailability(visual_studio_code=True, jetbrains_rider=None),
    )
    restored = EnvironmentSnapshot.from_json(snapshot.to_json())
    assert restored.to_dict() == snapshot.to_dict()
    assert restored.virtualization.hyper_v_available is None
    assert restored.editors.visual_studio_code is True
    assert restored.git.version == "git version 2.45"


def test_parse_valid_partial_output_preserves_unknown(monkeypatch, tmp_path: Path):
    _patch_native_probe(monkeypatch, _result())
    snapshot = EnvironmentDiscovery(cache_enabled=False, data_dir=tmp_path).discover()
    assert snapshot.success is False
    assert snapshot.virtualization.hyper_v_available is None
    assert snapshot.virtualization.windows_sandbox_available is False
    assert snapshot.development_tools.scoop_available is None
    assert snapshot.git.version == "git version 2.45.0"


def test_cache_uses_same_canonical_snapshot_schema(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr(discovery_module, "_system_powershell", lambda: Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
    monkeypatch.setattr(discovery_module, "run_bounded", lambda *_args, **_kwargs: calls.append(1) or _result())
    discovery = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path)
    first = discovery.discover()
    assert discovery.cache_file.is_file()
    cached_json = json.loads(discovery.cache_file.read_text(encoding="utf-8"))
    assert cached_json == first.to_dict()
    second = discovery.discover()
    assert calls == [1]
    assert second.to_dict() == first.to_dict()


def test_force_refresh_runs_discovery_again(monkeypatch, tmp_path: Path):
    calls = []
    monkeypatch.setattr(discovery_module, "_system_powershell", lambda: Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"))
    monkeypatch.setattr(discovery_module, "run_bounded", lambda *_args, **_kwargs: calls.append(1) or _result())
    discovery = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path)
    discovery.discover()
    discovery.discover(force_refresh=True)
    assert calls == [1, 1]


def test_nonzero_process_marks_parseable_snapshot_degraded(monkeypatch, tmp_path: Path):
    good = dict(MOCK_DISCOVERY_OUTPUT)
    good["success"] = True
    good["errors"] = []
    _patch_native_probe(monkeypatch, _result(returncode=5, stdout=json.dumps(good), stderr="probe process failed"))
    snapshot = EnvironmentDiscovery(cache_enabled=False, data_dir=tmp_path).discover()
    assert snapshot.success is False
    assert any("exited with code 5" in error for error in snapshot.errors)


def test_invalid_json_returns_degraded_fallback(monkeypatch, tmp_path: Path):
    _patch_native_probe(monkeypatch, _result(returncode=1, stdout="not-json", stderr="bad"))
    snapshot = EnvironmentDiscovery(cache_enabled=False, data_dir=tmp_path).discover()
    assert snapshot.success is False
    assert snapshot.virtualization.windows_sandbox_available is None


def test_timeout_returns_canonical_degraded_snapshot(monkeypatch, tmp_path: Path):
    _patch_native_probe(monkeypatch, _result(returncode=None, stdout="", timed_out=True))
    snapshot = EnvironmentDiscovery(cache_enabled=False, data_dir=tmp_path).discover()
    payload = snapshot.to_dict()
    assert snapshot.success is False
    assert any("timed out" in error for error in snapshot.errors)
    assert payload["probe_states"]["wsl"] == "unknown"
    assert payload["probe_states"]["windows_sandbox"] == "unknown"


def test_cache_load_rejects_oversized_file(monkeypatch, tmp_path: Path):
    discovery = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path)
    discovery.cache_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(discovery_module, "MAX_CACHE_BYTES", 8)
    discovery.cache_file.write_bytes(b"{" + b"x" * 20 + b"}")
    assert discovery._load_cache() is None


def test_generation_change_prevents_stale_discovery_from_resurrecting_cache(monkeypatch, tmp_path: Path):
    discovery = EnvironmentDiscovery(cache_enabled=True, data_dir=tmp_path)
    snapshot = EnvironmentSnapshot(timestamp=datetime.now(), success=False, errors=["stale"])

    def run_and_mutate():
        assert invalidate_environment_cache(tmp_path) is True
        return snapshot

    monkeypatch.setattr(discovery, "_run_discovery", run_and_mutate)
    result = discovery.discover(force_refresh=True)
    assert result is snapshot
    assert not discovery.cache_file.exists()
    assert discovery.generation_file.exists()
