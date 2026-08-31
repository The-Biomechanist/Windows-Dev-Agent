from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one source match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


# Repair the three PowerShell tokens caught during static review before execution.
ps = Path("src/discovery/discovery.ps1")
text = ps.read_text(encoding="utf-8")
count = text.count("\nelif (")
if count != 3:
    raise SystemExit(f"discovery.ps1: expected three elif tokens, found {count}")
ps.write_text(text.replace("\nelif (", "\nelseif ("), encoding="utf-8", newline="\n")

replace_once(
    "src/mcp/server.py",
    '''from src.observability.trace import history_log_files\n''',
    '''from src.observability.trace import history_log_files\nfrom src.windows_state import query_wsl_route_state\n''',
)
replace_once(
    "src/mcp/server.py",
    '''    selected = expected\n    if selected == "wsl" and not _wsl_executable():\n        return None, "Windows-owned WSL executable is not available for linux_compatibility", "unavailable"\n    if selected == "dev_container":\n''',
    '''    selected = expected\n    if selected == "wsl":\n        wsl_executable = _wsl_executable()\n        wsl_state = query_wsl_route_state(wsl_executable)\n        if wsl_state.available is not True:\n            status = "unavailable" if wsl_state.available is False else "unknown"\n            return None, wsl_state.reason or "WSL route state could not be established", status\n    if selected == "dev_container":\n''',
)

# Existing MCP tests use deliberately fake WSL executable paths. Make the
# native route precondition explicit in those fixtures rather than letting the
# host runner's real WSL registration leak into unit-test behavior.
path = Path("tests/test_mcp_runtime.py")
text = path.read_text(encoding="utf-8")
needle = 'from src.mcp import server\n'
if text.count(needle) != 1:
    raise SystemExit("test_mcp_runtime.py: server import witness changed")
text = text.replace(needle, needle + 'from src.windows_state import WslRouteState\n', 1)

for function_name in (
    "test_plan_first_execution_refuses_missing_or_changed_executable_identity",
    "test_wsl_route_uses_windows_owned_identity_and_project_cd",
    "test_captured_sandbox_spawn_failure_is_not_reported_as_started",
):
    marker = f"def {function_name}(tmp_path: Path, monkeypatch):\n"
    if marker not in text:
        raise SystemExit(f"test_mcp_runtime.py: missing {function_name}")
    start = text.index(marker) + len(marker)
    trusted_line_end = text.index("\n", start) + 1
    insertion = '    monkeypatch.setattr(server, "query_wsl_route_state", lambda _exe: WslRouteState(True, "Ubuntu"))\n'
    text = text[:trusted_line_end] + insertion + text[trusted_line_end:]

path.write_text(text, encoding="utf-8", newline="\n")

# Update the native discovery test from the obsolete inbox-feature coupling to
# the new Store/inbox-agnostic and filesystem-authoritative contracts.
path = Path("tests/test_discovery_windows.py")
text = path.read_text(encoding="utf-8")
old = '''def test_wsl_enabled_but_missing_executable_is_not_swallowed_as_plain_missing():\n    text = SCRIPT.read_text(encoding="utf-8")\n    inconsistent = 'elseif ($wslFeature.available -eq $true -and $wslExePresent -eq $false)'\n    generic_missing = 'elseif ($wslFeature.available -eq $false -or $wslExePresent -eq $false)'\n    assert inconsistent in text and generic_missing in text\n    assert text.index(inconsistent) < text.index(generic_missing)\n    block = text[text.index(inconsistent):text.index(generic_missing)]\n    assert 'Add-DiscoveryError "WSL feature is enabled but wsl.exe was not found"' in block\n    assert '$wslInstalled = $null' in block\n\n\n'''
new = '''def test_wsl_discovery_is_store_inbox_agnostic_and_default_distro_aware():\n    text = SCRIPT.read_text(encoding="utf-8")\n    assert 'MicrosoftCorporationII.WindowsSubsystemForLinux' in text\n    assert 'Services\\\\WslService' in text\n    assert 'Services\\\\LxssManager' in text\n    assert 'HKCU:\\\\Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Lxss' in text\n    assert 'Get-WslPolicyValue "AllowWSL"' in text\n    assert 'wsl_available = $wslAvailable' in text\n    assert 'wsl_default_distro = $wslDefaultDistro' in text\n\n\ndef test_dev_drive_discovery_uses_native_persistent_volume_state_not_label():\n    text = SCRIPT.read_text(encoding="utf-8")\n    assert 'FSCTL_QUERY_PERSISTENT_VOLUME_STATE' in text\n    assert 'PERSISTENT_VOLUME_STATE_DEV_VOLUME' in text\n    assert 'TryIsDeveloperVolume' in text\n    assert 'FileSystemLabel -match "DevDrive"' not in text\n\n\n'''
if text.count(old) != 1:
    raise SystemExit("test_discovery_windows.py: obsolete WSL contract witness changed")
text = text.replace(old, new, 1)
text = text.replace(
    '    assert payload["virtualization"]["wsl_installed"] in {True, False, None}\n',
    '    assert payload["virtualization"]["wsl_installed"] in {True, False, None}\n'
    '    assert payload["virtualization"]["wsl_available"] in {True, False, None}\n'
    '    assert payload["virtualization"]["dev_drive_inventory_state"] in {"available", "unknown"}\n',
    1,
)
path.write_text(text, encoding="utf-8", newline="\n")

print("native discovery authority transforms applied")
