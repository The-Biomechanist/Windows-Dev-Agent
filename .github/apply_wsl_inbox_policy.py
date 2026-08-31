from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one source match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


# Keep the router's static WSL precondition on Windows registry/service authority.
replace_once(
    "src/windows_state.py",
    '''from dataclasses import dataclass\nimport os\nfrom pathlib import Path\nfrom typing import Any, Optional\n''',
    '''from dataclasses import dataclass\nfrom pathlib import Path\nfrom typing import Optional\n''',
)
replace_once(
    "src/windows_state.py",
    '''WSL_POLICY_KEY = r"SOFTWARE\\Policies\\WSL"\nWSL_USER_KEY = r"Software\\Microsoft\\Windows\\CurrentVersion\\Lxss"\n''',
    '''WSL_POLICY_KEY = r"SOFTWARE\\Policies\\WSL"\nWSL_USER_KEY = r"Software\\Microsoft\\Windows\\CurrentVersion\\Lxss"\nWSL_STORE_SERVICE_KEY = r"SYSTEM\\CurrentControlSet\\Services\\WslService"\nWSL_INBOX_SERVICE_KEY = r"SYSTEM\\CurrentControlSet\\Services\\LxssManager"\n''',
)
replace_once(
    "src/windows_state.py",
    '''def _registered_default_distribution() -> tuple[Optional[str], Optional[int], Optional[bool], Optional[str]]:\n''',
    '''def _service_registered(key_path: str) -> tuple[Optional[bool], Optional[str]]:\n    """Establish whether one WSL service implementation is registered."""\n    if winreg is None:\n        return None, "Windows registry access is unavailable"\n    access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)\n    try:\n        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access):\n            return True, None\n    except FileNotFoundError:\n        return False, None\n    except OSError as exc:\n        return None, f"WSL service registration could not be established: {exc}"\n\n\ndef _registered_default_distribution() -> tuple[Optional[str], Optional[int], Optional[bool], Optional[str]]:\n''',
)
replace_once(
    "src/windows_state.py",
    '''    if allow_wsl == 0:\n        return WslRouteState(False, reason="Windows policy disables WSL")\n\n    default_name, distro_version, inventory_established, distro_error = _registered_default_distribution()\n''',
    '''    if allow_wsl == 0:\n        return WslRouteState(False, reason="Windows policy disables WSL")\n\n    store_service, service_error = _service_registered(WSL_STORE_SERVICE_KEY)\n    if service_error:\n        return WslRouteState(None, reason=service_error)\n    inbox_service, service_error = _service_registered(WSL_INBOX_SERVICE_KEY)\n    if service_error:\n        return WslRouteState(None, reason=service_error)\n    if store_service is not True and inbox_service is not True:\n        return WslRouteState(False, reason="No registered WSL service implementation is available")\n    if store_service is not True and inbox_service is True:\n        allow_inbox, policy_error = _read_policy_dword("AllowInboxWSL")\n        if policy_error:\n            return WslRouteState(None, reason=policy_error)\n        if allow_inbox == 0:\n            return WslRouteState(False, reason="Windows policy disables Inbox WSL and Store WSL is unavailable")\n\n    default_name, distro_version, inventory_established, distro_error = _registered_default_distribution()\n''',
)
replace_once(
    "src/windows_state.py",
    '''    if distro_version == 1:\n        allow_wsl1, policy_error = _read_policy_dword("AllowWSL1")\n        if policy_error:\n            return WslRouteState(None, default_distribution=default_name, reason=policy_error)\n        if allow_wsl1 == 0:\n            return WslRouteState(False, default_distribution=default_name, reason="Windows policy disables WSL 1")\n\n    return WslRouteState(True, default_distribution=default_name)\n''',
    '''    if distro_version in {1, None}:\n        allow_wsl1, policy_error = _read_policy_dword("AllowWSL1")\n        if policy_error:\n            return WslRouteState(None, default_distribution=default_name, reason=policy_error)\n        if allow_wsl1 == 0:\n            if distro_version == 1:\n                return WslRouteState(False, default_distribution=default_name, reason="Windows policy disables WSL 1")\n            return WslRouteState(\n                None,\n                default_distribution=default_name,\n                reason="Default WSL distribution version is unknown while Windows policy disables WSL 1",\n            )\n\n    return WslRouteState(True, default_distribution=default_name)\n''',
)

# Broad PowerShell discovery mirrors the same Store-vs-Inbox policy distinction.
replace_once(
    "src/discovery/discovery.ps1",
    '''$wslServicePresent = $null\ntry {\n    $wslServicePresent = (Test-Path -LiteralPath "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\WslService") -or\n        (Test-Path -LiteralPath "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\LxssManager")\n}\ncatch {\n    Add-DiscoveryError "WSL service registration was not established: $($_.Exception.Message)"\n}\n''',
    '''$wslStoreServicePresent = $null\ntry {\n    $wslStoreServicePresent = Test-Path -LiteralPath "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\WslService"\n}\ncatch {\n    Add-DiscoveryError "Store WSL service registration was not established: $($_.Exception.Message)"\n}\n\n$wslInboxServicePresent = $null\ntry {\n    $wslInboxServicePresent = Test-Path -LiteralPath "HKLM:\\SYSTEM\\CurrentControlSet\\Services\\LxssManager"\n}\ncatch {\n    Add-DiscoveryError "Inbox WSL service registration was not established: $($_.Exception.Message)"\n}\n''',
)
replace_once(
    "src/discovery/discovery.ps1",
    '''$wslInstalled = $null\nif ($wslExePresent -eq $false) {\n    $wslInstalled = $false\n}\nelseif ($wslExePresent -eq $true -and (\n    $wslServicePresent -eq $true -or $storeWslPresent -eq $true -or $wslFeature.available -eq $true\n)) {\n    $wslInstalled = $true\n}\nelseif ($wslExePresent -eq $true -and\n      $wslServicePresent -eq $false -and\n      $storeWslPresent -eq $false -and\n      $wslFeature.available -eq $false) {\n    $wslInstalled = $false\n}\n\n$allowWsl = Get-WslPolicyValue "AllowWSL"\n$allowWsl1 = Get-WslPolicyValue "AllowWSL1"\n$wslPolicyAllowed = if ($allowWsl.established -eq $true) {\n    -not ($allowWsl.configured -eq $true -and $allowWsl.value -eq 0)\n} else { $null }\n$wsl1PolicyAllowed = if ($allowWsl1.established -eq $true) {\n    -not ($allowWsl1.configured -eq $true -and $allowWsl1.value -eq 0)\n} else { $null }\n''',
    '''$storeWslImplementationPresent = $null\nif ($storeWslPresent -eq $true -or $wslStoreServicePresent -eq $true) {\n    $storeWslImplementationPresent = $true\n}\nelseif ($storeWslPresent -eq $false -and $wslStoreServicePresent -eq $false) {\n    $storeWslImplementationPresent = $false\n}\n\n$inboxWslImplementationPresent = $null\nif ($wslFeature.available -eq $true -or $wslInboxServicePresent -eq $true) {\n    $inboxWslImplementationPresent = $true\n}\nelseif ($wslFeature.available -eq $false -and $wslInboxServicePresent -eq $false) {\n    $inboxWslImplementationPresent = $false\n}\n\n$wslInstalled = $null\nif ($wslExePresent -eq $false) {\n    $wslInstalled = $false\n}\nelseif ($wslExePresent -eq $true -and (\n    $storeWslImplementationPresent -eq $true -or $inboxWslImplementationPresent -eq $true\n)) {\n    $wslInstalled = $true\n}\nelseif ($wslExePresent -eq $true -and\n      $storeWslImplementationPresent -eq $false -and\n      $inboxWslImplementationPresent -eq $false) {\n    $wslInstalled = $false\n}\n\n$allowWsl = Get-WslPolicyValue "AllowWSL"\n$allowInboxWsl = Get-WslPolicyValue "AllowInboxWSL"\n$allowWsl1 = Get-WslPolicyValue "AllowWSL1"\n$wslPolicyAllowed = if ($allowWsl.established -eq $true) {\n    -not ($allowWsl.configured -eq $true -and $allowWsl.value -eq 0)\n} else { $null }\n$inboxWslPolicyAllowed = if ($allowInboxWsl.established -eq $true) {\n    -not ($allowInboxWsl.configured -eq $true -and $allowInboxWsl.value -eq 0)\n} else { $null }\n$wsl1PolicyAllowed = if ($allowWsl1.established -eq $true) {\n    -not ($allowWsl1.configured -eq $true -and $allowWsl1.value -eq 0)\n} else { $null }\n\n$wslImplementationAllowed = $null\nif ($storeWslImplementationPresent -eq $true) {\n    # AllowInboxWSL does not restrict Store/lifted WSL.\n    $wslImplementationAllowed = $true\n}\nelseif ($storeWslImplementationPresent -eq $false -and $inboxWslImplementationPresent -eq $true) {\n    $wslImplementationAllowed = $inboxWslPolicyAllowed\n}\nelseif ($storeWslImplementationPresent -eq $false -and $inboxWslImplementationPresent -eq $false) {\n    $wslImplementationAllowed = $false\n}\nelseif ($inboxWslImplementationPresent -eq $true -and $inboxWslPolicyAllowed -eq $true) {\n    # Inbox is definitely available and allowed even if Store state is unresolved.\n    $wslImplementationAllowed = $true\n}\n''',
)
replace_once(
    "src/discovery/discovery.ps1",
    '''$wslAvailable = $null\nif ($wslInstalled -eq $false -or $wslPolicyAllowed -eq $false) {\n    $wslAvailable = $false\n}\nelseif ($wslInstalled -eq $true -and $wslPolicyAllowed -eq $true -and $wslDistroInventoryEstablished) {\n    if ([string]::IsNullOrWhiteSpace($wslDefaultDistro)) {\n        $wslAvailable = $false\n    }\n    elseif ($wslDefaultVersion -eq 1 -and $wsl1PolicyAllowed -eq $false) {\n        $wslAvailable = $false\n    }\n    elseif ($wslDefaultVersion -eq 1 -and $wsl1PolicyAllowed -eq $null) {\n        $wslAvailable = $null\n    }\n    else {\n        $wslAvailable = $true\n    }\n}\n''',
    '''$wslAvailable = $null\nif ($wslInstalled -eq $false -or $wslPolicyAllowed -eq $false -or $wslImplementationAllowed -eq $false) {\n    $wslAvailable = $false\n}\nelseif ($wslInstalled -eq $true -and\n        $wslPolicyAllowed -eq $true -and\n        $wslImplementationAllowed -eq $true -and\n        $wslDistroInventoryEstablished) {\n    if ([string]::IsNullOrWhiteSpace($wslDefaultDistro)) {\n        $wslAvailable = $false\n    }\n    elseif ($wslDefaultVersion -eq 1 -and $wsl1PolicyAllowed -eq $false) {\n        $wslAvailable = $false\n    }\n    elseif ($wslDefaultVersion -eq 1 -and $wsl1PolicyAllowed -eq $null) {\n        $wslAvailable = $null\n    }\n    elseif ($null -eq $wslDefaultVersion -and $wsl1PolicyAllowed -eq $false) {\n        # The default may be WSL1, which policy would block; preserve uncertainty.\n        $wslAvailable = $null\n    }\n    else {\n        $wslAvailable = $true\n    }\n}\n''',
)

# Tests make service implementation state explicit and cover AllowInboxWSL.
path = Path("tests/test_windows_state.py")
text = path.read_text(encoding="utf-8")
insert_after = '''def _executable() -> str:\n    return str(Path(sys.executable).resolve())\n\n\n'''
helper = '''def _mock_services(monkeypatch, *, store: bool = True, inbox: bool = False) -> None:\n    def registered(key_path: str):\n        if key_path == windows_state.WSL_STORE_SERVICE_KEY:\n            return store, None\n        if key_path == windows_state.WSL_INBOX_SERVICE_KEY:\n            return inbox, None\n        raise AssertionError(key_path)\n\n    monkeypatch.setattr(windows_state, "_service_registered", registered)\n\n\n'''
if text.count(insert_after) != 1:
    raise SystemExit("test_windows_state.py: helper insertion witness changed")
text = text.replace(insert_after, insert_after + helper, 1)
for function_name in (
    "test_wsl_route_requires_registered_default_distribution",
    "test_wsl_route_accepts_store_or_inbox_agnostic_registered_default",
    "test_wsl1_route_respects_wsl1_policy",
):
    marker = f"def {function_name}(monkeypatch):\n"
    if text.count(marker) != 1:
        raise SystemExit(f"test_windows_state.py: missing {function_name}")
    text = text.replace(marker, marker + "    _mock_services(monkeypatch)\n", 1)

append = '''\n\ndef test_inbox_wsl_route_respects_inbox_policy(monkeypatch):\n    _mock_services(monkeypatch, store=False, inbox=True)\n\n    def policy(name: str):\n        if name == "AllowInboxWSL":\n            return 0, None\n        return None, None\n\n    monkeypatch.setattr(windows_state, "_read_policy_dword", policy)\n    monkeypatch.setattr(\n        windows_state,\n        "_registered_default_distribution",\n        lambda: ("Ubuntu", 2, True, None),\n    )\n    state = windows_state.query_wsl_route_state(_executable())\n    assert state.available is False\n    assert "Inbox WSL" in (state.reason or "")\n\n\ndef test_store_wsl_route_is_not_blocked_by_inbox_policy(monkeypatch):\n    _mock_services(monkeypatch, store=True, inbox=True)\n\n    def policy(name: str):\n        if name == "AllowInboxWSL":\n            return 0, None\n        return None, None\n\n    monkeypatch.setattr(windows_state, "_read_policy_dword", policy)\n    monkeypatch.setattr(\n        windows_state,\n        "_registered_default_distribution",\n        lambda: ("Ubuntu", 2, True, None),\n    )\n    state = windows_state.query_wsl_route_state(_executable())\n    assert state.available is True\n\n\ndef test_unknown_default_distro_version_is_unknown_when_wsl1_is_disabled(monkeypatch):\n    _mock_services(monkeypatch)\n\n    def policy(name: str):\n        if name == "AllowWSL1":\n            return 0, None\n        return None, None\n\n    monkeypatch.setattr(windows_state, "_read_policy_dword", policy)\n    monkeypatch.setattr(\n        windows_state,\n        "_registered_default_distribution",\n        lambda: ("Ubuntu", None, True, None),\n    )\n    state = windows_state.query_wsl_route_state(_executable())\n    assert state.available is None\n    assert "version is unknown" in (state.reason or "")\n'''
text += append
path.write_text(text, encoding="utf-8", newline="\n")

# Static discovery tests pin the Inbox-vs-Store distinction.
path = Path("tests/test_discovery_windows.py")
text = path.read_text(encoding="utf-8")
needle = '''    assert 'Get-WslPolicyValue "AllowWSL"' in text\n    assert 'wsl_available = $wslAvailable' in text\n'''
replacement = '''    assert 'Get-WslPolicyValue "AllowWSL"' in text\n    assert 'Get-WslPolicyValue "AllowInboxWSL"' in text\n    assert 'Get-WslPolicyValue "AllowWSL1"' in text\n    assert 'Services\\\\WslService' in text\n    assert 'Services\\\\LxssManager' in text\n    assert 'wsl_available = $wslAvailable' in text\n'''
if text.count(needle) != 1:
    raise SystemExit("test_discovery_windows.py: WSL policy witness changed")
path.write_text(text.replace(needle, replacement, 1), encoding="utf-8", newline="\n")

# Public contract names all three native policy surfaces.
replace_once(
    "README.md",
    '''Store WSL, native service registration, machine WSL policy, and the current user's registered distributions are evaluated separately.''',
    '''Store WSL, native service registration, global/Inbox/WSL1 machine policy, and the current user's registered distributions are evaluated separately.''',
)
replace_once(
    "CHANGELOG.md",
    '''- Make WSL discovery Store/inbox agnostic: distinguish control-plane installation from usable routing, honor machine WSL/WSL1 policy, read the current user's native WSL distribution registration, and require a valid default distribution before advertising `linux_compatibility`.''',
    '''- Make WSL discovery Store/inbox agnostic: distinguish control-plane installation from usable routing, honor global WSL, Inbox WSL, and WSL1 machine policy, read the current user's native WSL distribution registration, and require a valid default distribution before advertising `linux_compatibility`.''',
)

print("WSL Inbox policy authority aligned")
