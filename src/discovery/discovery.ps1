# Windows Dev Agent - noninteractive Windows environment discovery.
# Availability values are tri-state: $true observed present/enabled, $false
# observed absent/disabled, and $null when the probe could not establish state.
#
# This broad snapshot deliberately avoids launching external developer tools.
# Focused version probing belongs to tool_discover, where it can be time-bounded
# and requested only when the result can change a routing decision.

$ErrorActionPreference = "Stop"

$discoveryResult = @{
    timestamp = Get-Date -Format "o"
    success = $true
    errors = @()
    system = @{}
    virtualization = @{}
    development_tools = @{}
    runtimes = @{}
    git = @{}
    editors = @{}
}

function Add-DiscoveryError {
    param([string]$Message)
    $script:discoveryResult.success = $false
    $script:discoveryResult.errors += $Message
}

function Test-CommandAvailable {
    param([string]$Name)
    try {
        return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
    }
    catch {
        return $null
    }
}

function Get-OptionalFeatureProbe {
    param([string]$FeatureName)
    try {
        $feature = Get-WindowsOptionalFeature -Online -FeatureName $FeatureName -ErrorAction Stop
        if ($null -eq $feature) {
            throw "feature query returned no result"
        }
        return @{
            available = ($feature.State -eq "Enabled")
            state = [string]$feature.State
        }
    }
    catch {
        Add-DiscoveryError "Optional feature '$FeatureName' was not established: $($_.Exception.Message)"
        return @{ available = $null; state = "unknown" }
    }
}

# System and hardware state used by routing/resource decisions.
try {
    $osInfo = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    $processors = @(Get-CimInstance -ClassName Win32_Processor -ErrorAction Stop)
    $computer = Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop
    $firstProcessor = $processors | Select-Object -First 1
    $discoveryResult.system = @{
        os_name = [string]$osInfo.Caption
        os_version = [string]$osInfo.Version
        os_build = [string]$osInfo.BuildNumber
        architecture = [string]$osInfo.OSArchitecture
        processor_count = $processors.Count
        processor_name = if ($null -ne $firstProcessor) { [string]$firstProcessor.Name } else { "" }
        total_physical_memory_gb = if ($computer.TotalPhysicalMemory) {
            [math]::Round($computer.TotalPhysicalMemory / 1GB, 2)
        } else { 0.0 }
    }
}
catch {
    Add-DiscoveryError "System information was not established: $($_.Exception.Message)"
    $discoveryResult.system = @{
        os_name = "Unknown"; os_version = ""; os_build = ""; architecture = ""
        processor_count = 0; processor_name = ""; total_physical_memory_gb = 0.0
    }
}

# Virtualization and isolation prerequisites. Optional-feature identity is the
# authority for enabled/disabled state; executable presence alone is not enough.
$hyperv = Get-OptionalFeatureProbe "Microsoft-Hyper-V"
$sandbox = Get-OptionalFeatureProbe "Containers-DisposableClientVM"
$wslFeature = Get-OptionalFeatureProbe "Microsoft-Windows-Subsystem-Linux"
$wslExePresent = $null
try {
    $wslExePresent = Test-Path -LiteralPath "$env:WINDIR\System32\wsl.exe"
}
catch {
    Add-DiscoveryError "WSL executable presence was not established: $($_.Exception.Message)"
}

$wslInstalled = $null
if ($wslFeature.available -eq $true -and $wslExePresent -eq $true) {
    $wslInstalled = $true
}
elseif ($wslFeature.available -eq $true -and $wslExePresent -eq $false) {
    # The feature claims enabled while the required executable is absent. That
    # is an observed inconsistency, not ordinary "missing" state.
    Add-DiscoveryError "WSL feature is enabled but wsl.exe was not found"
    $wslInstalled = $null
}
elseif ($wslFeature.available -eq $false -or $wslExePresent -eq $false) {
    $wslInstalled = $false
}

$devDrives = @()
try {
    if ($null -ne (Get-Command Get-Volume -ErrorAction SilentlyContinue)) {
        $devDrives = @(Get-Volume -ErrorAction Stop | Where-Object { $_.FileSystemLabel -match "DevDrive" } | ForEach-Object {
            @{
                drive_letter = [string]$_.DriveLetter
                label = [string]$_.FileSystemLabel
                size_gb = [math]::Round($_.Size / 1GB, 2)
                free_space_gb = [math]::Round($_.SizeRemaining / 1GB, 2)
            }
        })
    }
}
catch {
    Add-DiscoveryError "Dev Drive inventory was not established: $($_.Exception.Message)"
}

$discoveryResult.virtualization = @{
    hyper_v_available = $hyperv.available
    hyper_v_state = $hyperv.state
    wsl_installed = $wslInstalled
    wsl_version = $null
    wsl_distros = @()
    windows_sandbox_available = $sandbox.available
    windows_sandbox_state = $sandbox.state
    dev_drives = $devDrives
}

# Package managers and development tools: presence only. Version details are
# intentionally delegated to the focused tool_discover MCP tool.
try {
    $discoveryResult.development_tools = @{
        winget_available = Test-CommandAvailable "winget"
        chocolatey_available = Test-CommandAvailable "choco"
        scoop_available = Test-CommandAvailable "scoop"
        git_available = Test-CommandAvailable "git"
        docker_available = Test-CommandAvailable "docker"
        vscode_available = (Test-CommandAvailable "code") -or (Test-Path "C:\Program Files\Microsoft VS Code\Code.exe")
        visual_studio_available = (Test-CommandAvailable "devenv") -or (Test-Path "C:\Program Files\Microsoft Visual Studio")
    }
}
catch {
    Add-DiscoveryError "Development-tool inventory was not established: $($_.Exception.Message)"
    $discoveryResult.development_tools = @{
        winget_available = $null; chocolatey_available = $null; scoop_available = $null
        git_available = $null; docker_available = $null; vscode_available = $null
        visual_studio_available = $null
    }
}

# Runtime availability only. Avoid running arbitrary PATH-resolved binaries in
# the broad discovery pass; focused version probes use Python-side timeouts.
$runtimes = @{}
foreach ($runtime in @(
    @{ key = "python"; command = "python" },
    @{ key = "node"; command = "node" },
    @{ key = "rust"; command = "rustc" },
    @{ key = "golang"; command = "go" }
)) {
    $available = Test-CommandAvailable $runtime.command
    $runtimes[$runtime.key] = @{ available = $available; version = $null; versions = @() }
}
$dotnetAvailable = Test-CommandAvailable "dotnet"
$runtimes.dotnet = @{ available = $dotnetAvailable; version = $null; versions = @() }
$discoveryResult.runtimes = $runtimes

# Git identity is intentionally not collected; routing only needs availability.
$gitAvailable = Test-CommandAvailable "git"
$discoveryResult.git = @{
    available = $gitAvailable
    version = $null
}

# Editor presence only; no user/editor configuration is collected here.
try {
    $discoveryResult.editors = @{
        visual_studio_code = (Test-CommandAvailable "code") -or (Test-Path "C:\Program Files\Microsoft VS Code\Code.exe")
        visual_studio = (Test-CommandAvailable "devenv") -or (Test-Path "C:\Program Files\Microsoft Visual Studio")
        jetbrains_rider = [bool](Get-ChildItem "C:\Program Files\JetBrains\Rider*" -ErrorAction SilentlyContinue | Select-Object -First 1)
        jetbrains_pycharm = [bool](Get-ChildItem "C:\Program Files\JetBrains\PyCharm*" -ErrorAction SilentlyContinue | Select-Object -First 1)
        jetbrains_clion = [bool](Get-ChildItem "C:\Program Files\JetBrains\CLion*" -ErrorAction SilentlyContinue | Select-Object -First 1)
    }
}
catch {
    Add-DiscoveryError "Editor inventory was not established: $($_.Exception.Message)"
    $discoveryResult.editors = @{
        visual_studio_code = $null; visual_studio = $null; jetbrains_rider = $null
        jetbrains_pycharm = $null; jetbrains_clion = $null
    }
}

$discoveryResult | ConvertTo-Json -Depth 10 -Compress
