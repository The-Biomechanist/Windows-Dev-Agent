# Windows Dev Agent - noninteractive Windows environment discovery.
# Availability values are tri-state: $true observed present/enabled, $false
# observed absent/disabled, and $null when the probe could not establish state.

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

function Get-FirstOutputLine {
    param(
        [string]$Command,
        [string[]]$Arguments
    )
    try {
        $output = & $Command @Arguments 2>&1
        if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) {
            return $null
        }
        $first = $output | Select-Object -First 1
        if ($null -eq $first) { return $null }
        return ([string]$first).Trim()
    }
    catch {
        return $null
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

# Virtualization and isolation prerequisites.
$hyperv = Get-OptionalFeatureProbe "Microsoft-Hyper-V"
$sandbox = Get-OptionalFeatureProbe "Containers-DisposableVM"
$wslInstalled = $null
$wslVersion = $null
$wslDistros = @()
try {
    $wslInstalled = Test-Path -LiteralPath "$env:WINDIR\System32\wsl.exe"
    if ($wslInstalled) {
        $wslVersion = Get-FirstOutputLine "wsl.exe" @("--version")
        try {
            $wslDistros = @(& wsl.exe --list --quiet 2>$null | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
        }
        catch {
            Add-DiscoveryError "WSL distro inventory was not established: $($_.Exception.Message)"
        }
    }
}
catch {
    Add-DiscoveryError "WSL availability was not established: $($_.Exception.Message)"
    $wslInstalled = $null
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
    wsl_version = $wslVersion
    wsl_distros = $wslDistros
    windows_sandbox_available = $sandbox.available
    windows_sandbox_state = $sandbox.state
    dev_drives = $devDrives
}

# Package managers and developer tools. Get-Command/Test-Path produce a real
# present/absent observation; a section-level failure leaves values unknown.
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

# Runtime availability/version state.
$runtimes = @{}
foreach ($runtime in @(
    @{ key = "python"; command = "python"; args = @("--version") },
    @{ key = "node"; command = "node"; args = @("--version") },
    @{ key = "rust"; command = "rustc"; args = @("--version") },
    @{ key = "golang"; command = "go"; args = @("version") }
)) {
    $available = Test-CommandAvailable $runtime.command
    $version = if ($available -eq $true) { Get-FirstOutputLine $runtime.command $runtime.args } else { $null }
    $runtimes[$runtime.key] = @{ available = $available; version = $version; versions = @() }
}

$dotnetAvailable = Test-CommandAvailable "dotnet"
$dotnetVersions = @()
if ($dotnetAvailable -eq $true) {
    try {
        $dotnetVersions = @(& dotnet --list-sdks 2>$null | ForEach-Object { ([string]$_).Split()[0] } | Where-Object { $_ })
    }
    catch {
        Add-DiscoveryError ".NET SDK versions were not established: $($_.Exception.Message)"
    }
}
$runtimes.dotnet = @{ available = $dotnetAvailable; version = $null; versions = $dotnetVersions }
$discoveryResult.runtimes = $runtimes

# Git identity is intentionally not collected; routing only needs availability/version.
$gitAvailable = Test-CommandAvailable "git"
$discoveryResult.git = @{
    available = $gitAvailable
    version = if ($gitAvailable -eq $true) { Get-FirstOutputLine "git" @("--version") } else { $null }
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
