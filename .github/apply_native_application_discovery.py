from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one source match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="\n")


replace_once(
    "src/discovery/discovery.ps1",
    '''function Test-CommandAvailable {\n    param([string]$Name)\n    try {\n        return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)\n    }\n    catch {\n        return $null\n    }\n}\n\n''',
    '''function Get-WdaApplicationSearchPath {\n    # WDA intentionally does not grant executable authority to the process cwd,\n    # empty PATH entries, or relative PATH entries. PowerShell remains the\n    # command-resolution authority after that explicit policy is applied.\n    try {\n        $current = [IO.Path]::GetFullPath([Environment]::CurrentDirectory).TrimEnd(\n            [IO.Path]::DirectorySeparatorChar,\n            [IO.Path]::AltDirectorySeparatorChar\n        )\n        $safe = @()\n        $seen = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)\n        foreach ($raw in ($env:PATH -split [IO.Path]::PathSeparator)) {\n            $entry = [string]$raw\n            if ([string]::IsNullOrWhiteSpace($entry)) { continue }\n            $entry = [Environment]::ExpandEnvironmentVariables($entry.Trim().Trim('"'))\n            if (-not [IO.Path]::IsPathRooted($entry)) { continue }\n            try {\n                $full = [IO.Path]::GetFullPath($entry).TrimEnd(\n                    [IO.Path]::DirectorySeparatorChar,\n                    [IO.Path]::AltDirectorySeparatorChar\n                )\n            }\n            catch {\n                continue\n            }\n            if ([StringComparer]::OrdinalIgnoreCase.Equals($full, $current)) { continue }\n            if ($seen.Add($full)) {\n                $safe += $full\n            }\n        }\n        return @{ established = $true; path = ($safe -join [IO.Path]::PathSeparator) }\n    }\n    catch {\n        Add-DiscoveryError "Executable search authority was not established: $($_.Exception.Message)"\n        return @{ established = $false; path = $null }\n    }\n}\n\n$WdaApplicationSearchPath = Get-WdaApplicationSearchPath\n\nfunction Test-CommandAvailable {\n    param([string]$Name)\n    if ($script:WdaApplicationSearchPath.established -ne $true) { return $null }\n    $originalPath = $env:PATH\n    try {\n        $env:PATH = [string]$script:WdaApplicationSearchPath.path\n        $command = Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1\n        return $null -ne $command\n    }\n    catch {\n        return $null\n    }\n    finally {\n        $env:PATH = $originalPath\n    }\n}\n\n''',
)

# Static contract and live Windows test coverage.
path = Path("tests/test_discovery_windows.py")
text = path.read_text(encoding="utf-8")
insert = """

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
    command = rf'''\n$ErrorActionPreference = 'Stop'\n$env:PATH = '.;{tmp_path};' + $env:PATH\n$null = . '{SCRIPT}'\nfunction wda-function-only {{ 'function' }}\nSet-Alias -Name wda-alias-only -Value Get-Date\nif (Test-CommandAvailable 'wda-function-only') {{ throw 'function counted as application' }}\nif (Test-CommandAvailable 'wda-alias-only') {{ throw 'alias counted as application' }}\nif (Test-CommandAvailable 'wda-fake') {{ throw 'process cwd application was trusted' }}\nif (-not (Test-CommandAvailable 'python')) {{ throw 'real PATH application was lost' }}\n'''
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
"""
anchor = '''def test_native_environment_discovery_round_trips_its_own_output(tmp_path: Path):\n'''
if text.count(anchor) != 1:
    raise SystemExit("tests/test_discovery_windows.py: insertion anchor changed")
text = text.replace(anchor, insert + "\n\n" + anchor, 1)
path.write_text(text, encoding="utf-8", newline="\n")

replace_once(
    "README.md",
    '''Native discovery is performed with Windows-owned PowerShell and returns one canonical `EnvironmentSnapshot` shape even when the probe degrades or fails unexpectedly.''',
    '''Native discovery is performed with Windows-owned PowerShell and returns one canonical `EnvironmentSnapshot` shape even when the probe degrades or fails unexpectedly. Broad developer-tool/runtime presence uses PowerShell `Get-Command -CommandType Application` rather than accepting aliases, functions, cmdlets, or scripts; WDA first removes process-cwd, empty, and relative PATH entries so the discovery fact uses the same explicit executable-authority policy as execution.''',
)
replace_once(
    "CHANGELOG.md",
    '''- Remove Windows current-directory executable authority from ordinary tool resolution. Bare tool names are resolved from absolute inherited `PATH` entries with cwd/empty/relative entries excluded, avoiding Python 3.11's unconditional cwd-first `shutil.which()` behavior and the conditional cwd-first behavior in newer Python releases.\n''',
    '''- Remove Windows current-directory executable authority from ordinary tool resolution. Bare tool names are resolved from absolute inherited `PATH` entries with cwd/empty/relative entries excluded, avoiding Python 3.11's unconditional cwd-first `shutil.which()` behavior and the conditional cwd-first behavior in newer Python releases. Broad PowerShell discovery applies the same search-authority policy and asks `Get-Command -CommandType Application`, so aliases/functions/cmdlets/scripts are not misreported as installed executable tools.\n''',
)

print("native PowerShell application discovery transform applied")
