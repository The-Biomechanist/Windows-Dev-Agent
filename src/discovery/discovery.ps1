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

function Get-WslPolicyValue {
    param([string]$Name)
    try {
        $policyPath = "HKLM:\SOFTWARE\Policies\WSL"
        if (-not (Test-Path -LiteralPath $policyPath)) {
            return @{ established = $true; configured = $false; value = $null }
        }
        $policy = Get-ItemProperty -LiteralPath $policyPath -ErrorAction Stop
        $property = $policy.PSObject.Properties[$Name]
        if ($null -eq $property) {
            return @{ established = $true; configured = $false; value = $null }
        }
        return @{ established = $true; configured = $true; value = [int]$property.Value }
    }
    catch {
        Add-DiscoveryError "WSL policy '$Name' was not established: $($_.Exception.Message)"
        return @{ established = $false; configured = $null; value = $null }
    }
}

function Initialize-DevDriveNativeProbe {
    $existing = ([System.Management.Automation.PSTypeName]'WdaNative.DevDriveProbe').Type
    if ($null -ne $existing) { return $true }
    try {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace WdaNative
{
    public static class DevDriveProbe
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct FILE_FS_PERSISTENT_VOLUME_INFORMATION
        {
            public uint VolumeFlags;
            public uint FlagMask;
            public uint Version;
            public uint Reserved;
        }

        private const uint FILE_READ_ATTRIBUTES = 0x00000080;
        private const uint FILE_SHARE_READ = 0x00000001;
        private const uint FILE_SHARE_WRITE = 0x00000002;
        private const uint FILE_SHARE_DELETE = 0x00000004;
        private const uint OPEN_EXISTING = 3;
        private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
        private const uint FSCTL_QUERY_PERSISTENT_VOLUME_STATE = 0x0009023C;
        private const uint PERSISTENT_VOLUME_STATE_DEV_VOLUME = 0x00002000;

        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        private static extern SafeFileHandle CreateFileW(
            string lpFileName,
            uint dwDesiredAccess,
            uint dwShareMode,
            IntPtr lpSecurityAttributes,
            uint dwCreationDisposition,
            uint dwFlagsAndAttributes,
            IntPtr hTemplateFile);

        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool DeviceIoControl(
            SafeFileHandle hDevice,
            uint dwIoControlCode,
            IntPtr lpInBuffer,
            uint nInBufferSize,
            IntPtr lpOutBuffer,
            uint nOutBufferSize,
            out uint lpBytesReturned,
            IntPtr lpOverlapped);

        public static bool TryIsDeveloperVolume(string volumeGuidPath, out bool isDeveloperVolume, out int errorCode)
        {
            isDeveloperVolume = false;
            errorCode = 0;
            if (String.IsNullOrWhiteSpace(volumeGuidPath))
            {
                errorCode = 87;
                return false;
            }

            string path = volumeGuidPath.TrimEnd('\\');
            using (SafeFileHandle handle = CreateFileW(
                path,
                FILE_READ_ATTRIBUTES,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                IntPtr.Zero,
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS,
                IntPtr.Zero))
            {
                if (handle.IsInvalid)
                {
                    errorCode = Marshal.GetLastWin32Error();
                    return false;
                }

                FILE_FS_PERSISTENT_VOLUME_INFORMATION input = new FILE_FS_PERSISTENT_VOLUME_INFORMATION();
                input.FlagMask = PERSISTENT_VOLUME_STATE_DEV_VOLUME;
                input.Version = 1;
                FILE_FS_PERSISTENT_VOLUME_INFORMATION output = new FILE_FS_PERSISTENT_VOLUME_INFORMATION();
                int size = Marshal.SizeOf(typeof(FILE_FS_PERSISTENT_VOLUME_INFORMATION));
                IntPtr inputPtr = Marshal.AllocHGlobal(size);
                IntPtr outputPtr = Marshal.AllocHGlobal(size);
                try
                {
                    Marshal.StructureToPtr(input, inputPtr, false);
                    uint bytesReturned;
                    bool ok = DeviceIoControl(
                        handle,
                        FSCTL_QUERY_PERSISTENT_VOLUME_STATE,
                        inputPtr,
                        (uint)size,
                        outputPtr,
                        (uint)size,
                        out bytesReturned,
                        IntPtr.Zero);
                    if (!ok)
                    {
                        errorCode = Marshal.GetLastWin32Error();
                        return false;
                    }
                    output = (FILE_FS_PERSISTENT_VOLUME_INFORMATION)Marshal.PtrToStructure(
                        outputPtr,
                        typeof(FILE_FS_PERSISTENT_VOLUME_INFORMATION));
                    isDeveloperVolume = (output.VolumeFlags & PERSISTENT_VOLUME_STATE_DEV_VOLUME) != 0;
                    return true;
                }
                finally
                {
                    Marshal.FreeHGlobal(inputPtr);
                    Marshal.FreeHGlobal(outputPtr);
                }
            }
        }
    }
}
'@ -Language CSharp -ErrorAction Stop
        return $true
    }
    catch {
        Add-DiscoveryError "Native Dev Drive probe could not be initialized: $($_.Exception.Message)"
        return $false
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

# Virtualization and isolation prerequisites. Hyper-V and Windows Sandbox are
# Windows optional components. WSL may instead be serviced by the Store, so its
# operational state is derived from native executable/service/package/registry
# authorities rather than the legacy inbox component alone.
$hyperv = Get-OptionalFeatureProbe "Microsoft-Hyper-V"
$sandbox = Get-OptionalFeatureProbe "Containers-DisposableClientVM"
$wslFeature = Get-OptionalFeatureProbe "Microsoft-Windows-Subsystem-Linux"

$wslExePresent = $null
try {
    $wslExePresent = Test-Path -LiteralPath "$env:WINDIR\System32\wsl.exe" -PathType Leaf
}
catch {
    Add-DiscoveryError "WSL executable presence was not established: $($_.Exception.Message)"
}

$wslServicePresent = $null
try {
    $wslServicePresent = (Test-Path -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\WslService") -or
        (Test-Path -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\LxssManager")
}
catch {
    Add-DiscoveryError "WSL service registration was not established: $($_.Exception.Message)"
}

$storeWslPresent = $null
try {
    if ($null -ne (Get-Command Get-AppxPackage -ErrorAction SilentlyContinue)) {
        $storeWslPresent = @(
            Get-AppxPackage -Name "MicrosoftCorporationII.WindowsSubsystemForLinux" -ErrorAction Stop
        ).Count -gt 0
    }
}
catch {
    Add-DiscoveryError "Store WSL package state was not established: $($_.Exception.Message)"
}

$wslInstalled = $null
if ($wslExePresent -eq $false) {
    $wslInstalled = $false
}
elseif ($wslExePresent -eq $true -and (
    $wslServicePresent -eq $true -or $storeWslPresent -eq $true -or $wslFeature.available -eq $true
)) {
    $wslInstalled = $true
}
elseif ($wslExePresent -eq $true -and
      $wslServicePresent -eq $false -and
      $storeWslPresent -eq $false -and
      $wslFeature.available -eq $false) {
    $wslInstalled = $false
}

$allowWsl = Get-WslPolicyValue "AllowWSL"
$allowWsl1 = Get-WslPolicyValue "AllowWSL1"
$wslPolicyAllowed = if ($allowWsl.established -eq $true) {
    -not ($allowWsl.configured -eq $true -and $allowWsl.value -eq 0)
} else { $null }
$wsl1PolicyAllowed = if ($allowWsl1.established -eq $true) {
    -not ($allowWsl1.configured -eq $true -and $allowWsl1.value -eq 0)
} else { $null }

$wslDistros = @()
$wslDefaultDistro = $null
$wslDefaultVersion = $null
$wslDistroInventoryEstablished = $false
try {
    $lxssPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss"
    if (Test-Path -LiteralPath $lxssPath) {
        $root = Get-Item -LiteralPath $lxssPath -ErrorAction Stop
        $defaultId = [string]$root.GetValue("DefaultDistribution")
        foreach ($distroKey in @(Get-ChildItem -LiteralPath $lxssPath -ErrorAction Stop)) {
            $name = [string]$distroKey.GetValue("DistributionName")
            if (-not [string]::IsNullOrWhiteSpace($name)) {
                $wslDistros += $name
                if (-not [string]::IsNullOrWhiteSpace($defaultId) -and $distroKey.PSChildName -ieq $defaultId) {
                    $wslDefaultDistro = $name
                    $version = $distroKey.GetValue("Version")
                    if ($null -ne $version) { $wslDefaultVersion = [int]$version }
                }
            }
        }
    }
    $wslDistroInventoryEstablished = $true
}
catch {
    Add-DiscoveryError "Registered WSL distribution state was not established: $($_.Exception.Message)"
}

$wslAvailable = $null
if ($wslInstalled -eq $false -or $wslPolicyAllowed -eq $false) {
    $wslAvailable = $false
}
elseif ($wslInstalled -eq $true -and $wslPolicyAllowed -eq $true -and $wslDistroInventoryEstablished) {
    if ([string]::IsNullOrWhiteSpace($wslDefaultDistro)) {
        $wslAvailable = $false
    }
    elseif ($wslDefaultVersion -eq 1 -and $wsl1PolicyAllowed -eq $false) {
        $wslAvailable = $false
    }
    elseif ($wslDefaultVersion -eq 1 -and $wsl1PolicyAllowed -eq $null) {
        $wslAvailable = $null
    }
    else {
        $wslAvailable = $true
    }
}

# Dev Drive identity is a persistent filesystem volume flag, not a volume label.
# Dev Drives are ReFS volumes; ReFS narrows the candidate set and the Win32
# FSCTL_QUERY_PERSISTENT_VOLUME_STATE flag establishes the actual identity.
$devDrives = @()
$devDriveInventoryState = "unknown"
try {
    if ($null -eq (Get-Command Get-Volume -ErrorAction SilentlyContinue)) {
        throw "Get-Volume is unavailable"
    }
    $candidateVolumes = @(
        Get-Volume -ErrorAction Stop | Where-Object {
            $_.FileSystemType -eq "ReFS" -and -not [string]::IsNullOrWhiteSpace([string]$_.Path)
        }
    )
    $devDriveInventoryState = "available"
    if ($candidateVolumes.Count -gt 0 -and -not (Initialize-DevDriveNativeProbe)) {
        $devDriveInventoryState = "unknown"
    }
    elseif ($candidateVolumes.Count -gt 0) {
        foreach ($volume in $candidateVolumes) {
            $isDeveloperVolume = $false
            $nativeError = 0
            $established = [WdaNative.DevDriveProbe]::TryIsDeveloperVolume(
                [string]$volume.Path,
                [ref]$isDeveloperVolume,
                [ref]$nativeError
            )
            if (-not $established) {
                $devDriveInventoryState = "unknown"
                Add-DiscoveryError "Dev Drive state for volume '$($volume.Path)' was not established (Win32 error $nativeError)"
                continue
            }
            if ($isDeveloperVolume) {
                $devDrives += @{
                    drive_letter = [string]$volume.DriveLetter
                    label = [string]$volume.FileSystemLabel
                    size_gb = [math]::Round($volume.Size / 1GB, 2)
                    free_space_gb = [math]::Round($volume.SizeRemaining / 1GB, 2)
                }
            }
        }
    }
}
catch {
    $devDriveInventoryState = "unknown"
    Add-DiscoveryError "Dev Drive inventory was not established: $($_.Exception.Message)"
}

$discoveryResult.virtualization = @{
    hyper_v_available = $hyperv.available
    hyper_v_state = $hyperv.state
    wsl_installed = $wslInstalled
    wsl_available = $wslAvailable
    wsl_default_distro = $wslDefaultDistro
    wsl_version = $null
    wsl_distros = $wslDistros
    windows_sandbox_available = $sandbox.available
    windows_sandbox_state = $sandbox.state
    dev_drive_inventory_state = $devDriveInventoryState
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
