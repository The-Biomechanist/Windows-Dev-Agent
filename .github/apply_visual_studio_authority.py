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
    '''function Test-CommandAvailable {\n    param([string]$Name)\n    if ($script:WdaApplicationSearchPath.established -ne $true) { return $null }\n    $originalPath = $env:PATH\n    try {\n        $env:PATH = [string]$script:WdaApplicationSearchPath.path\n        $command = Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1\n        return $null -ne $command\n    }\n    catch {\n        return $null\n    }\n    finally {\n        $env:PATH = $originalPath\n    }\n}\n\n''',
    '''function Test-CommandAvailable {\n    param([string]$Name)\n    if ($script:WdaApplicationSearchPath.established -ne $true) { return $null }\n    $originalPath = $env:PATH\n    try {\n        $env:PATH = [string]$script:WdaApplicationSearchPath.path\n        $command = Get-Command -Name $Name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1\n        return $null -ne $command\n    }\n    catch {\n        return $null\n    }\n    finally {\n        $env:PATH = $originalPath\n    }\n}\n\nfunction Get-VisualStudioAvailability {\n    # A PATH-resolved devenv.exe is positive evidence. Otherwise query the\n    # Visual Studio Installer's maintained native locator. Directory names are\n    # not installation authority, and an unavailable locator leaves state unknown.\n    $devenvAvailable = Test-CommandAvailable "devenv"\n    if ($devenvAvailable -eq $true) { return $true }\n\n    $locatorCandidates = @()\n    foreach ($folderName in @("ProgramFilesX86", "ProgramFiles")) {\n        try {\n            $programFiles = [Environment]::GetFolderPath($folderName)\n        }\n        catch {\n            continue\n        }\n        if ([string]::IsNullOrWhiteSpace($programFiles)) { continue }\n        $candidate = Join-Path $programFiles "Microsoft Visual Studio\\Installer\\vswhere.exe"\n        if ($locatorCandidates -notcontains $candidate) {\n            $locatorCandidates += $candidate\n        }\n    }\n\n    $locator = $locatorCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1\n    if (-not $locator) { return $null }\n\n    try {\n        $installationPath = & $locator -prerelease -latest -property installationPath 2>$null\n        if ($LASTEXITCODE -ne 0) {\n            Add-DiscoveryError "Visual Studio Installer locator exited $LASTEXITCODE"\n            return $null\n        }\n        return -not [string]::IsNullOrWhiteSpace(([string]($installationPath | Select-Object -First 1)))\n    }\n    catch {\n        Add-DiscoveryError "Visual Studio installation state was not established: $($_.Exception.Message)"\n        return $null\n    }\n}\n\n''',
)

replace_once(
    "src/discovery/discovery.ps1",
    '''# Package managers and development tools: presence only. Version details are\n# intentionally delegated to the focused tool_discover MCP tool.\ntry {\n    $discoveryResult.development_tools = @{\n''',
    '''# Package managers and development tools: presence only. Version details are\n# intentionally delegated to the focused tool_discover MCP tool.\n$visualStudioAvailable = Get-VisualStudioAvailability\ntry {\n    $discoveryResult.development_tools = @{\n''',
)
replace_once(
    "src/discovery/discovery.ps1",
    '''        vscode_available = (Test-CommandAvailable "code") -or (Test-Path "C:\\Program Files\\Microsoft VS Code\\Code.exe")\n        visual_studio_available = (Test-CommandAvailable "devenv") -or (Test-Path "C:\\Program Files\\Microsoft Visual Studio")\n''',
    '''        vscode_available = (Test-CommandAvailable "code") -or (Test-Path "C:\\Program Files\\Microsoft VS Code\\Code.exe")\n        visual_studio_available = $visualStudioAvailable\n''',
)
replace_once(
    "src/discovery/discovery.ps1",
    '''        visual_studio_code = (Test-CommandAvailable "code") -or (Test-Path "C:\\Program Files\\Microsoft VS Code\\Code.exe")\n        visual_studio = (Test-CommandAvailable "devenv") -or (Test-Path "C:\\Program Files\\Microsoft Visual Studio")\n''',
    '''        visual_studio_code = (Test-CommandAvailable "code") -or (Test-Path "C:\\Program Files\\Microsoft VS Code\\Code.exe")\n        visual_studio = $visualStudioAvailable\n''',
)

path = Path("tests/test_discovery_windows.py")
text = path.read_text(encoding="utf-8")
anchor = '''def test_broad_tool_presence_uses_native_application_resolution_with_wda_path_policy():\n'''
insert = '''def test_visual_studio_presence_uses_installer_locator_not_directory_name():\n    text = SCRIPT.read_text(encoding="utf-8")\n    assert "Get-VisualStudioAvailability" in text\n    assert "Microsoft Visual Studio\\\\Installer\\\\vswhere.exe" in text\n    assert "-prerelease -latest -property installationPath" in text\n    assert 'Test-Path "C:\\\\Program Files\\\\Microsoft Visual Studio"' not in text\n    assert "visual_studio_available = $visualStudioAvailable" in text\n    assert "visual_studio = $visualStudioAvailable" in text\n\n\ndef test_visual_studio_locator_establishes_hosted_runner_instance():\n    command = rf'''\n$ErrorActionPreference = 'Stop'\n$null = . '{SCRIPT}'\n$result = Get-VisualStudioAvailability\nif ($result -ne $true) {{ throw "Visual Studio locator did not establish hosted IDE instance: $result" }}\n'''\n    result = subprocess.run(\n        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],\n        stdin=subprocess.DEVNULL,\n        capture_output=True,\n        text=True,\n        timeout=60,\n        check=False,\n    )\n    assert result.returncode == 0, result.stderr or result.stdout\n\n\n'''
if text.count(anchor) != 1:
    raise SystemExit("tests/test_discovery_windows.py: Visual Studio insertion anchor changed")
text = text.replace(anchor, insert + anchor, 1)
path.write_text(text, encoding="utf-8", newline="\n")

replace_once(
    "README.md",
    '''Broad developer-tool/runtime presence uses PowerShell `Get-Command -CommandType Application` rather than accepting aliases, functions, cmdlets, or scripts; WDA first removes process-cwd, empty, and relative PATH entries so the discovery fact uses the same explicit executable-authority policy as execution.''',
    '''Broad developer-tool/runtime presence uses PowerShell `Get-Command -CommandType Application` rather than accepting aliases, functions, cmdlets, or scripts; WDA first removes process-cwd, empty, and relative PATH entries so the discovery fact uses the same explicit executable-authority policy as execution. Visual Studio IDE presence is established through a PATH-resolved `devenv.exe` or Microsoft Visual Studio Installer's maintained `vswhere.exe` locator; a matching `Program Files` directory name is not treated as an installed IDE.''',
)
replace_once(
    "CHANGELOG.md",
    '''- Remove Windows current-directory executable authority from ordinary tool resolution. Bare tool names are resolved from absolute inherited `PATH` entries with cwd/empty/relative entries excluded, avoiding Python 3.11's unconditional cwd-first `shutil.which()` behavior and the conditional cwd-first behavior in newer Python releases. Broad PowerShell discovery applies the same search-authority policy and asks `Get-Command -CommandType Application`, so aliases/functions/cmdlets/scripts are not misreported as installed executable tools.\n''',
    '''- Remove Windows current-directory executable authority from ordinary tool resolution. Bare tool names are resolved from absolute inherited `PATH` entries with cwd/empty/relative entries excluded, avoiding Python 3.11's unconditional cwd-first `shutil.which()` behavior and the conditional cwd-first behavior in newer Python releases. Broad PowerShell discovery applies the same search-authority policy and asks `Get-Command -CommandType Application`, so aliases/functions/cmdlets/scripts are not misreported as installed executable tools. Visual Studio IDE presence uses the Microsoft Visual Studio Installer's fixed `vswhere.exe` locator rather than a directory-name heuristic.\n''',
)

print("Visual Studio installer authority transform applied")
