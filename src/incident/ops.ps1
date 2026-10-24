# Windows Dev Agent incident operations. See docs/INCIDENT_RESPONSE.md.
#
# Reads one request from stdin: {"op": "<name>", "payload": {...}}.
# Writes one reply: {"ok": true, "result": {...}} or {"ok": false, "error": "..."}.
#
# Values arrive as JSON, never spliced into command text. Every operation checks
# its own preconditions again on this host, because the plan was validated earlier
# and the machine may have changed since.

$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$ProtectedRoots = @(
    'c:\windows\',
    'c:\program files\',
    'c:\program files (x86)\',
    'c:\programdata\microsoft\windows defender\'
)
$ProtectedProcessNames = @(
    'system', 'idle', 'smss', 'csrss', 'wininit', 'winlogon', 'services', 'lsass',
    'lsaiso', 'svchost', 'msmpeng', 'nissrv', 'sihost', 'dwm', 'fontdrvhost'
)
$RunKeys = @{
    'HKCU' = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
    'HKLM' = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run'
}
$UserStartupFolder = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$CommonStartupFolder = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\StartUp'
$HostsPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'

function Test-Protected([string]$Path) {
    $lower = $Path.ToLowerInvariant()
    foreach ($root in $ProtectedRoots) {
        if ($lower.StartsWith($root)) { return $true }
    }
    return $false
}

function Get-SignatureState([string]$Path) {
    try {
        $sig = Get-AuthenticodeSignature -LiteralPath $Path
        $signer = $null
        if ($sig.SignerCertificate) { $signer = $sig.SignerCertificate.Subject }
        return @{ signed = ($sig.Status -eq 'Valid'); signer = $signer }
    } catch {
        return @{ signed = $null; signer = $null }
    }
}

function Get-ExecutablePath([string]$CommandLine) {
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return $null }
    $text = [Environment]::ExpandEnvironmentVariables($CommandLine.Trim())
    if ($text -match '^([A-Za-z]:\\.*?\.(exe|com|bat|cmd|ps1|scr))(\s|$)') { return $matches[1] }
    if ($text.StartsWith('"')) {
        $end = $text.IndexOf('"', 1)
        if ($end -gt 0) { return $text.Substring(1, $end - 1) }
        return $null
    }
    return ($text -split '\s+')[0]
}

function Get-IsoTime($Value) {
    return $Value.ToUniversalTime().ToString('o')
}

function Invoke-Collect {
    $warnings = New-Object System.Collections.Generic.List[string]

    $connections = @{}
    try {
        foreach ($c in (Get-NetTCPConnection -State Established -ErrorAction Stop)) {
            $owner = [int]$c.OwningProcess
            if (-not $connections.ContainsKey($owner)) {
                $connections[$owner] = New-Object System.Collections.Generic.List[string]
            }
            $connections[$owner].Add([string]$c.RemoteAddress)
        }
    } catch {
        $warnings.Add("network connections unavailable: $($_.Exception.Message)")
    }

    $processes = New-Object System.Collections.Generic.List[object]
    foreach ($p in (Get-Process | Select-Object -First 400)) {
        $path = $null
        try { $path = $p.Path } catch { $path = $null }
        $started = $null
        try { $started = Get-IsoTime $p.StartTime } catch { $started = $null }
        $sig = @{ signed = $null; signer = $null }
        if ($path) { $sig = Get-SignatureState $path }
        $count = 0
        $remote = @()
        if ($connections.ContainsKey([int]$p.Id)) {
            $list = $connections[[int]$p.Id]
            $count = $list.Count
            $remote = @($list | Select-Object -Unique | Select-Object -First 10)
        }
        $processes.Add(@{
            pid = [int]$p.Id
            name = [string]$p.ProcessName
            path = $path
            started = $started
            signed = $sig.signed
            signer = $sig.signer
            connections = $count
            remote = $remote
        })
    }

    $persistence = New-Object System.Collections.Generic.List[object]
    foreach ($hive in @('HKCU', 'HKLM')) {
        $item = Get-ItemProperty -Path $RunKeys[$hive] -ErrorAction SilentlyContinue
        if ($null -eq $item) { continue }
        foreach ($prop in $item.PSObject.Properties) {
            if ($prop.Name -like 'PS*') { continue }
            $command = [string]$prop.Value
            $path = Get-ExecutablePath $command
            $sig = @{ signed = $null; signer = $null }
            if ($path -match '^[A-Za-z]:\\' -and (Test-Path -LiteralPath $path -PathType Leaf)) {
                $sig = Get-SignatureState $path
            }
            $persistence.Add(@{
                kind = 'run_key'; hive = $hive; name = $prop.Name; location = $RunKeys[$hive]
                command = $command; path = $path; signed = $sig.signed; signer = $sig.signer
            })
        }
    }

    try {
        foreach ($task in (Get-ScheduledTask -ErrorAction Stop | Where-Object { $_.State -ne 'Disabled' })) {
            foreach ($action in $task.Actions) {
                if (-not $action.Execute) { continue }
                $exe = [Environment]::ExpandEnvironmentVariables([string]$action.Execute).Trim('"')
                $sig = @{ signed = $null; signer = $null }
                $path = $null
                if ($exe -match '^[A-Za-z]:\\' -and (Test-Path -LiteralPath $exe -PathType Leaf)) {
                    $path = $exe
                    $sig = Get-SignatureState $exe
                }
                $persistence.Add(@{
                    kind = 'scheduled_task'; name = [string]$task.TaskName; task_path = [string]$task.TaskPath
                    location = [string]$task.TaskPath
                    command = ("$exe " + [string]$action.Arguments).Trim()
                    path = $path; signed = $sig.signed; signer = $sig.signer
                })
            }
        }
    } catch {
        $warnings.Add("scheduled tasks unavailable: $($_.Exception.Message)")
    }

    foreach ($folder in @($UserStartupFolder, $CommonStartupFolder)) {
        if (-not (Test-Path -LiteralPath $folder)) { continue }
        foreach ($file in (Get-ChildItem -LiteralPath $folder -File -ErrorAction SilentlyContinue)) {
            $sig = @{ signed = $null; signer = $null }
            if (@('.exe', '.bat', '.cmd', '.ps1', '.vbs', '.js') -contains $file.Extension.ToLowerInvariant()) {
                $sig = Get-SignatureState $file.FullName
            }
            $persistence.Add(@{
                kind = 'startup_item'; name = $file.Name; location = $folder
                command = $file.Name; path = $file.FullName; signed = $sig.signed; signer = $sig.signer
            })
        }
    }

    try {
        foreach ($svc in (Get-CimInstance -ClassName Win32_Service -Filter "StartMode='Auto'" -ErrorAction Stop)) {
            $path = Get-ExecutablePath ([string]$svc.PathName)
            $sig = @{ signed = $null; signer = $null }
            if ($path -match '^[A-Za-z]:\\' -and (Test-Path -LiteralPath $path -PathType Leaf)) {
                $sig = Get-SignatureState $path
            }
            $persistence.Add(@{
                kind = 'service'; name = [string]$svc.Name; location = 'services'
                command = [string]$svc.PathName; path = $path; signed = $sig.signed; signer = $sig.signer
            })
        }
    } catch {
        $warnings.Add("services unavailable: $($_.Exception.Message)")
    }

    $defender = @{ realtime_protection = $null; recent_threats = @() }
    try {
        $status = Get-MpComputerStatus -ErrorAction Stop
        $defender.realtime_protection = [bool]$status.RealTimeProtectionEnabled
    } catch {
        $warnings.Add("Defender status unavailable: $($_.Exception.Message)")
    }
    try {
        $threats = @(Get-MpThreatDetection -ErrorAction Stop | Select-Object -Last 10)
        $defender.recent_threats = @($threats | ForEach-Object {
            @{ name = [string]$_.ThreatName; time = (Get-IsoTime $_.InitialDetectionTime) }
        })
    } catch {
        $warnings.Add("Defender history unavailable: $($_.Exception.Message)")
    }

    $proxy = @{ enabled = $null; server = $null }
    try {
        $inet = Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -ErrorAction Stop
        $proxy.enabled = [bool]($inet.ProxyEnable -eq 1)
        $proxy.server = [string]$inet.ProxyServer
    } catch {
        $warnings.Add("proxy settings unavailable: $($_.Exception.Message)")
    }

    $hosts = @{ non_default_entries = 0; sample = @() }
    try {
        $defaults = @('127.0.0.1 localhost', '::1 localhost')
        $lines = @(Get-Content -LiteralPath $HostsPath -ErrorAction Stop |
            ForEach-Object { $_.Trim() } |
            Where-Object { $_ -and -not $_.StartsWith('#') -and ($defaults -notcontains ($_ -replace '\s+', ' ')) })
        $hosts.non_default_entries = $lines.Count
        $hosts.sample = @($lines | Select-Object -First 10 | ForEach-Object { $_.Substring(0, [Math]::Min($_.Length, 160)) })
    } catch {
        $warnings.Add("hosts file unavailable: $($_.Exception.Message)")
    }

    return @{
        schema = 1
        collected_at = Get-IsoTime (Get-Date)
        processes = $processes.ToArray()
        persistence = $persistence.ToArray()
        defender = $defender
        proxy = $proxy
        hosts = $hosts
        warnings = $warnings.ToArray()
    }
}

function Invoke-QuarantineFile($P) {
    $path = [string]$P.path
    if (Test-Protected $path) { throw 'refusing: protected location' }
    $item = Get-Item -LiteralPath $path -Force
    if ($item.PSIsContainer) { throw 'refusing: target is a folder' }
    if (([int]$item.Attributes -band [int][IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'refusing: reparse point' }
    $hash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    $dir = Join-Path ([string]$P.quarantine_root) ([string]$P.exec_id)
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    $destination = Join-Path $dir ('{0:D2}-{1}' -f [int]$P.index, $item.Name)
    Move-Item -LiteralPath $path -Destination $destination -ErrorAction Stop
    return @{ original_path = $path; quarantined_path = $destination; sha256 = $hash }
}

function Invoke-UndoQuarantineFile($P) {
    $quarantined = [string]$P.quarantined_path
    $original = [string]$P.original_path
    if (-not (Test-Path -LiteralPath $quarantined -PathType Leaf)) { throw 'quarantined file is missing' }
    if (Test-Path -LiteralPath $original) { throw 'original location is occupied; restore by hand' }
    $hash = (Get-FileHash -LiteralPath $quarantined -Algorithm SHA256).Hash
    if ($hash -ne [string]$P.sha256) { throw 'quarantined file changed; not restoring it' }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $original) | Out-Null
    Move-Item -LiteralPath $quarantined -Destination $original -ErrorAction Stop
    return @{ restored_to = $original }
}

function Invoke-DisableRunValue($P) {
    $hive = [string]$P.hive
    if (-not $RunKeys.ContainsKey($hive)) { throw 'unsupported hive' }
    $key = $RunKeys[$hive]
    $name = [string]$P.name
    $value = [string](Get-ItemPropertyValue -Path $key -Name $name -ErrorAction Stop)
    Remove-ItemProperty -Path $key -Name $name -ErrorAction Stop
    return @{ hive = $hive; name = $name; value = $value }
}

function Invoke-UndoDisableRunValue($P) {
    $key = $RunKeys[[string]$P.hive]
    Set-ItemProperty -Path $key -Name ([string]$P.name) -Value ([string]$P.value) -Type String -ErrorAction Stop
    return @{ restored = [string]$P.name }
}

function Invoke-DisableScheduledTask($P) {
    $task = Get-ScheduledTask -TaskPath ([string]$P.task_path) -TaskName ([string]$P.task_name) -ErrorAction Stop
    $previous = [string]$task.State
    if ($previous -eq 'Disabled') { throw 'task is already disabled' }
    Disable-ScheduledTask -TaskPath ([string]$P.task_path) -TaskName ([string]$P.task_name) -ErrorAction Stop | Out-Null
    return @{ task_path = [string]$P.task_path; task_name = [string]$P.task_name; previous_state = $previous }
}

function Invoke-UndoDisableScheduledTask($P) {
    Enable-ScheduledTask -TaskPath ([string]$P.task_path) -TaskName ([string]$P.task_name) -ErrorAction Stop | Out-Null
    return @{ re_enabled = [string]$P.task_name }
}

function Invoke-BlockProgramOutbound($P) {
    $path = [string]$P.path
    if (Test-Protected $path) { throw 'refusing: protected location' }
    $rule = [string]$P.rule_name
    if (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue) { throw 'a rule with this name already exists' }
    New-NetFirewallRule -DisplayName $rule -Direction Outbound -Program $path -Action Block -Profile Any -ErrorAction Stop | Out-Null
    return @{ rule_name = $rule }
}

function Invoke-BlockRemoteAddress($P) {
    $rule = [string]$P.rule_name
    if (Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue) { throw 'a rule with this name already exists' }
    New-NetFirewallRule -DisplayName $rule -Direction Outbound -RemoteAddress ([string]$P.address) -Action Block -Profile Any -ErrorAction Stop | Out-Null
    return @{ rule_name = $rule }
}

function Invoke-UndoBlockRule($P) {
    Remove-NetFirewallRule -DisplayName ([string]$P.rule_name) -ErrorAction Stop
    return @{ removed = [string]$P.rule_name }
}

function Invoke-StopProcess($P) {
    $processId = [int]$P.pid
    $proc = Get-Process -Id $processId -ErrorAction Stop
    $path = $null
    try { $path = $proc.Path } catch { $path = $null }
    if ($proc.ProcessName -ne [string]$P.name) { throw 'process name changed' }
    if ($path -ne [string]$P.path) { throw 'process path changed or is not readable' }
    if ((Get-IsoTime $proc.StartTime) -ne [string]$P.started) { throw 'process start time changed; the pid was reused' }
    if ($ProtectedProcessNames -contains $proc.ProcessName.ToLowerInvariant()) { throw 'refusing: protected system process' }
    if (Test-Protected $path) { throw 'refusing: protected location' }
    Stop-Process -Id $processId -Force -ErrorAction Stop
    return @{ pid = $processId; name = [string]$P.name; path = $path }
}

try {
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $result = switch ([string]$request.op) {
        'collect' { Invoke-Collect }
        'quarantine_file' { Invoke-QuarantineFile $request.payload }
        'undo_quarantine_file' { Invoke-UndoQuarantineFile $request.payload }
        'disable_run_value' { Invoke-DisableRunValue $request.payload }
        'undo_disable_run_value' { Invoke-UndoDisableRunValue $request.payload }
        'disable_scheduled_task' { Invoke-DisableScheduledTask $request.payload }
        'undo_disable_scheduled_task' { Invoke-UndoDisableScheduledTask $request.payload }
        'block_program_outbound' { Invoke-BlockProgramOutbound $request.payload }
        'block_remote_address' { Invoke-BlockRemoteAddress $request.payload }
        'undo_block_rule' { Invoke-UndoBlockRule $request.payload }
        'stop_process' { Invoke-StopProcess $request.payload }
        default { throw "unknown operation: $($request.op)" }
    }
    @{ ok = $true; result = $result } | ConvertTo-Json -Depth 8 -Compress
} catch {
    @{ ok = $false; error = $_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
}
