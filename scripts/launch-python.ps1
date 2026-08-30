[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^src\.[A-Za-z0-9_.]+$')]
    [string]$Module,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = 'Stop'
$MinimumPython = [Version]'3.11'
$PluginRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$Candidates = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)

function Test-FullyQualifiedWindowsPath {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $false }

    # Windows PowerShell 5.1 runs on .NET Framework, which does not expose
    # Path.IsPathFullyQualified. Accept only drive-absolute, UNC, or extended
    # Windows paths; reject drive-relative (C:foo) and rooted-relative (\foo).
    if ($Path -match '^[A-Za-z]:[\\/]') { return $true }
    if ($Path -match '^\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$)') { return $true }
    if ($Path -match '^\\\\\?\\[A-Za-z]:\\') { return $true }
    if ($Path -match '^\\\\\?\\UNC\\[^\\]+\\[^\\]+(?:\\|$)') { return $true }
    return $false
}

function Add-PythonCandidate {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    try {
        $Full = [IO.Path]::GetFullPath($Path)
    } catch {
        return
    }
    if ([IO.Path]::GetFileName($Full) -ine 'python.exe') { return }
    if (Test-Path -LiteralPath $Full -PathType Leaf) {
        [void]$Candidates.Add($Full)
    }
}

# A host may supply one already-resolved interpreter identity. Relative values are
# rejected so this cannot reintroduce current-directory or PATH shadowing.
if (-not [string]::IsNullOrWhiteSpace($env:WINDOWS_DEV_AGENT_PYTHON)) {
    if (-not (Test-FullyQualifiedWindowsPath $env:WINDOWS_DEV_AGENT_PYTHON)) {
        [Console]::Error.WriteLine('WINDOWS_DEV_AGENT_PYTHON must be an absolute python.exe path.')
        exit 70
    }
    Add-PythonCandidate $env:WINDOWS_DEV_AGENT_PYTHON
}

# Prefer installation authorities that do not search the current project or PATH.
$RegistryRoots = @(
    'Registry::HKEY_CURRENT_USER\Software\Python\PythonCore',
    'Registry::HKEY_LOCAL_MACHINE\Software\Python\PythonCore',
    'Registry::HKEY_LOCAL_MACHINE\Software\WOW6432Node\Python\PythonCore'
)
foreach ($Root in $RegistryRoots) {
    if (-not (Test-Path -LiteralPath $Root)) { continue }
    foreach ($VersionKey in Get-ChildItem -LiteralPath $Root -ErrorAction SilentlyContinue) {
        $InstallKey = Join-Path $VersionKey.PSPath 'InstallPath'
        if (-not (Test-Path -LiteralPath $InstallKey)) { continue }
        try {
            $Properties = Get-ItemProperty -LiteralPath $InstallKey -ErrorAction Stop
            Add-PythonCandidate $Properties.ExecutablePath
            $DefaultPath = (Get-Item -LiteralPath $InstallKey -ErrorAction Stop).GetValue('')
            if ($DefaultPath) {
                Add-PythonCandidate (Join-Path ([string]$DefaultPath) 'python.exe')
            }
        } catch {
            continue
        }
    }
}

$InstallParents = @(
    (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs\Python'),
    ([Environment]::GetFolderPath('ProgramFiles')),
    ([Environment]::GetFolderPath('ProgramFilesX86'))
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
foreach ($Parent in $InstallParents) {
    if (-not (Test-Path -LiteralPath $Parent -PathType Container)) { continue }
    foreach ($Directory in Get-ChildItem -LiteralPath $Parent -Directory -ErrorAction SilentlyContinue) {
        if ($Parent -like '*\Programs\Python' -or $Directory.Name -like 'Python*') {
            Add-PythonCandidate (Join-Path $Directory.FullName 'python.exe')
        }
    }
}

$Usable = @()
foreach ($Candidate in $Candidates) {
    try {
        $VersionText = & $Candidate -I -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")' 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $VersionText) { continue }
        $Version = [Version]([string]$VersionText).Trim()
        if ($Version -lt $MinimumPython) { continue }
        $Usable += [PSCustomObject]@{ Path = $Candidate; Version = $Version }
    } catch {
        continue
    }
}

$Selected = $Usable | Sort-Object Version -Descending | Select-Object -First 1
if (-not $Selected) {
    [Console]::Error.WriteLine('Windows Dev Agent requires a host Python 3.11 or newer. No supported interpreter was found in the explicit host override, registered Python installations, or standard Windows installation locations.')
    exit 70
}

$env:WINDOWS_DEV_AGENT_PLUGIN_ROOT = $PluginRoot
$Bootstrap = "import os,runpy,sys; module=sys.argv[1]; sys.path.insert(0, os.environ['WINDOWS_DEV_AGENT_PLUGIN_ROOT']); sys.argv=[module,*sys.argv[2:]]; runpy.run_module(module, run_name='__main__')"
& $Selected.Path -I -c $Bootstrap $Module @RemainingArgs
exit $LASTEXITCODE
