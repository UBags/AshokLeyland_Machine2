# powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\EnableAllUpdates.ps1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Re-enables the updates that DisableAllUpdates.ps1 turned off on Windows 11.

.DESCRIPTION
    Run it from an elevated (Administrator) PowerShell, in the folder that holds this file,
    with the command on the first line. Like DisableAllUpdates.ps1, it re-runs itself as SYSTEM
    through a one-off scheduled task, which it deletes afterwards.

    What it restores, from C:\ProgramData\UpdateControl\state.json (saved by
    DisableAllUpdates.ps1 before it changed anything):
      - Registry values: each value is put back as it was. A value that did not exist before
        is deleted, and so is a policy key that did not exist before and is now empty.
      - Services: each service gets back the startup type it had. Services that were
        Automatic are started again.
      - Scheduled tasks: tasks that were enabled are enabled again; tasks that were already
        disabled stay disabled.

    If state.json is missing, it uses Windows 11 defaults instead:
      - the policy values that DisableAllUpdates.ps1 sets are deleted;
      - the services get their usual startup types;
      - all the update tasks are enabled.

    After a run with no failures, state.json is renamed to state.restored-<date>.json.
    Log: C:\ProgramData\UpdateControl\EnableAllUpdates.log

    Restart the PC afterwards.
#>
param([switch]$AsSystem)

$ErrorActionPreference = 'Stop'

$ScriptName    = 'EnableAllUpdates'
$StateDir      = Join-Path $env:ProgramData 'UpdateControl'
$StateFile     = Join-Path $StateDir 'state.json'
$LogFile       = Join-Path $StateDir "$ScriptName.log"
$DoneFile      = Join-Path $StateDir "$ScriptName.done"
$WorkerFile    = Join-Path $StateDir "$ScriptName.worker.ps1"
$TaskName      = "UpdateControl-$ScriptName"

# The same values as DisableAllUpdates.ps1. Default = the Windows value, used only when
# state.json is missing; without a Default, the value is deleted.
$WU = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
$Registry = @(
    @{ Path = $WU;      Name = 'DoNotConnectToWindowsUpdateInternetLocations' }
    @{ Path = $WU;      Name = 'DisableWindowsUpdateAccess' }
    @{ Path = $WU;      Name = 'SetDisableUXWUAccess' }
    @{ Path = $WU;      Name = 'ExcludeWUDriversInQualityUpdate' }
    @{ Path = $WU;      Name = 'WUServer' }
    @{ Path = $WU;      Name = 'WUStatusServer' }
    @{ Path = "$WU\AU"; Name = 'NoAutoUpdate' }
    @{ Path = "$WU\AU"; Name = 'UseWUServer' }
    @{ Path = "$WU\AU"; Name = 'NoAutoRebootWithLoggedOnUsers' }
    @{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\DriverSearching';
       Name = 'SearchOrderConfig'; Kind = 'DWord'; Default = 1 }
    @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Device Metadata'; Name = 'PreventDeviceMetadataFromNetwork' }
    @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore';           Name = 'AutoDownload' }
    @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\EdgeUpdate';             Name = 'UpdateDefault' }
    @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\EdgeUpdate';             Name = 'AutoUpdateCheckPeriodMinutes' }
)

# Usual Windows 11 startup types (2 = Automatic, 3 = Manual); used only when state.json is missing.
$DefaultServiceStart = [ordered]@{
    wuauserv = 3; UsoSvc = 2; WaaSMedicSvc = 3; uhssvc = 3; sedsvc = 3; edgeupdate = 2; edgeupdatem = 3
}

$TaskFilters = @(
    @{ Path = '\Microsoft\Windows\WindowsUpdate\';      Name = '*' }
    @{ Path = '\Microsoft\Windows\UpdateOrchestrator\'; Name = '*' }
    @{ Path = '\Microsoft\Windows\WaaSMedic\';          Name = '*' }
    @{ Path = '\Microsoft\Windows\rempl\';              Name = '*' }
    @{ Path = '\Microsoft\Windows\InstallService\';     Name = 'ScanForUpdates*' }
    @{ Path = '\';                                      Name = 'MicrosoftEdgeUpdateTask*' }
)

# ----------------------------------------------------------------------------------------
function Write-Log([string]$Message) {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Get-UpdateTasks {
    foreach ($f in $TaskFilters) {
        Get-ScheduledTask -TaskPath $f.Path -TaskName $f.Name -ErrorAction SilentlyContinue
    }
}

function Get-ServiceKey([string]$Name) { "HKLM:\SYSTEM\CurrentControlSet\Services\$Name" }

# Runs as SYSTEM. Returns the number of failures.
function Invoke-Worker {
    $failures = 0
    Write-Log "$ScriptName started as $([Security.Principal.WindowsIdentity]::GetCurrent().Name)"

    $saved = $null
    if (Test-Path $StateFile) {
        $saved = Get-Content -Path $StateFile -Raw | ConvertFrom-Json
        Write-Log "Restoring the state saved on $($saved.Created) ($StateFile)"
    } else {
        Write-Log "No saved state ($StateFile); using Windows 11 defaults."
    }

    # 1. Registry
    Write-Log '--- Policies and registry settings'
    if ($saved) {
        $entries = @($saved.Registry | Where-Object { $_ })
    } else {
        $entries = foreach ($r in $Registry) {
            [pscustomobject]@{
                Path       = $r.Path
                Name       = $r.Name
                KeyExisted = -not ($r.Path -like '*\Policies\*')
                Existed    = $r.ContainsKey('Default')
                Kind       = $r.Kind
                Value      = $r.Default
            }
        }
    }

    foreach ($e in $entries) {
        try {
            if ($e.Existed) {
                $value = $e.Value
                if ($e.Kind -eq 'Binary')      { $value = [byte[]]@($e.Value) }
                if ($e.Kind -eq 'MultiString') { $value = [string[]]@($e.Value) }
                if (-not (Test-Path $e.Path)) { New-Item -Path $e.Path -Force | Out-Null }
                New-ItemProperty -Path $e.Path -Name $e.Name -PropertyType $e.Kind -Value $value -Force | Out-Null
                Write-Log ('OK      {0}\{1} restored to {2}' -f $e.Path, $e.Name, ($value -join ','))
            } elseif ((Test-Path $e.Path) -and ((Get-Item -Path $e.Path).GetValueNames() -contains $e.Name)) {
                Remove-ItemProperty -Path $e.Path -Name $e.Name
                Write-Log ('OK      {0}\{1} removed' -f $e.Path, $e.Name)
            } else {
                Write-Log ('OK      {0}\{1} not present' -f $e.Path, $e.Name)
            }
        } catch {
            $failures++
            Write-Log ('FAILED  {0}\{1}: {2}' -f $e.Path, $e.Name, $_.Exception.Message)
        }
    }

    # Keys that did not exist before and are now empty, deepest first
    $newKeys = @($entries | Where-Object { -not $_.KeyExisted } | ForEach-Object { $_.Path } |
                 Sort-Object -Unique | Sort-Object -Property Length -Descending)
    foreach ($p in $newKeys) {
        try {
            if (Test-Path $p) {
                $key = Get-Item -Path $p
                if ($key.ValueCount -eq 0 -and $key.SubKeyCount -eq 0) {
                    Remove-Item -Path $p
                    Write-Log "OK      empty key $p removed"
                }
            }
        } catch {
            $failures++
            Write-Log "FAILED  key ${p}: $($_.Exception.Message)"
        }
    }

    # 2. Services
    Write-Log '--- Services'
    if ($saved) {
        $svcEntries = @($saved.Services | Where-Object { $_ })
    } else {
        $svcEntries = foreach ($kv in $DefaultServiceStart.GetEnumerator()) {
            [pscustomobject]@{ Name = $kv.Key; Start = $kv.Value }
        }
    }
    foreach ($s in $svcEntries) {
        $k = Get-ServiceKey $s.Name
        if (-not (Test-Path $k)) { Write-Log "SKIPPED service $($s.Name) (not installed)"; continue }
        try {
            Set-ItemProperty -Path $k -Name 'Start' -Value ([int]$s.Start) -Type DWord
            $now = (Get-ItemProperty -Path $k).Start
            if ($now -ne [int]$s.Start) { throw "Start is $now after the write" }
        } catch {
            $failures++
            Write-Log "FAILED  service $($s.Name): $($_.Exception.Message)"
            continue
        }
        $msg = "OK      service $($s.Name) startup type restored (Start = $($s.Start))"
        if ([int]$s.Start -eq 2) {
            try   { Start-Service -Name $s.Name; $msg += ', started' }
            catch { $msg += "; not started now ($($_.Exception.Message)); it starts after the restart" }
        }
        Write-Log $msg
    }

    # 3. Scheduled tasks
    Write-Log '--- Scheduled tasks'
    if ($saved) {
        $taskEntries = @($saved.Tasks | Where-Object { $_ })
    } else {
        $taskEntries = foreach ($t in Get-UpdateTasks) {
            [pscustomobject]@{ Path = $t.TaskPath; Name = $t.TaskName; Enabled = $true }
        }
    }
    foreach ($t in $taskEntries) {
        $full = "$($t.Path)$($t.Name)"
        if (-not $t.Enabled) { Write-Log "OK      task $full left disabled (it was disabled before)"; continue }
        if (-not (Get-ScheduledTask -TaskPath $t.Path -TaskName $t.Name -ErrorAction SilentlyContinue)) {
            Write-Log "SKIPPED task $full (no longer exists)"
            continue
        }
        try {
            Enable-ScheduledTask -TaskPath $t.Path -TaskName $t.Name | Out-Null
            Write-Log "OK      task $full enabled"
        } catch {
            $failures++
            Write-Log "FAILED  task ${full}: $($_.Exception.Message)"
        }
    }

    # 4. Saved state used up
    if ($saved -and $failures -eq 0) {
        $restored = Join-Path $StateDir ('state.restored-{0:yyyyMMdd-HHmmss}.json' -f (Get-Date))
        Move-Item -Path $StateFile -Destination $restored
        Write-Log "Saved state renamed to $restored"
    }

    Write-Log "--- Finished with $failures failure(s)"
    return $failures
}

# ----------------------------------------------------------------------------------------
if ($AsSystem) {
    try   { $failures = @(Invoke-Worker)[-1] }
    catch { Write-Log "FAILED  $($_.Exception.Message)"; $failures = 1 }
    Set-Content -Path $DoneFile -Value $failures
    exit 0
}

# Elevated user: run the worker as SYSTEM through a one-off scheduled task
New-Item -ItemType Directory -Path $StateDir -Force | Out-Null
Remove-Item -Path $LogFile, $DoneFile -ErrorAction SilentlyContinue
Copy-Item -LiteralPath $PSCommandPath -Destination $WorkerFile -Force

$psExe     = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action    = New-ScheduledTaskAction -Execute $psExe -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$WorkerFile`" -AsSystem"
$principal = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $TaskName -Action $action -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "Running as SYSTEM ..."
$started = Get-Date
Start-ScheduledTask -TaskName $TaskName
$deadline = $started.AddMinutes(10)
do {
    Start-Sleep -Seconds 2
    $taskState = (Get-ScheduledTask -TaskName $TaskName).State
    $info      = Get-ScheduledTaskInfo -TaskName $TaskName
    $ended     = ($taskState -ne 'Running') -and ($info.LastRunTime -ge $started.AddSeconds(-2))
} until ((Test-Path $DoneFile) -or $ended -or ((Get-Date) -gt $deadline))
Start-Sleep -Seconds 1

if (Test-Path $LogFile) {
    Get-Content -Path $LogFile | ForEach-Object {
        if ($_ -match 'FAILED') { Write-Host $_ -ForegroundColor Red } else { Write-Host $_ }
    }
}

if (Test-Path $DoneFile) {
    $failures = [int](Get-Content -Path $DoneFile -Raw)
} else {
    $failures = 1
    Write-Host ("The SYSTEM task did not finish (last result 0x{0:X8})." -f $info.LastTaskResult) -ForegroundColor Red
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Remove-Item -Path $WorkerFile, $DoneFile -ErrorAction SilentlyContinue

Write-Host ''
if ($failures -eq 0) {
    Write-Host 'Updates enabled again. Restart the PC, then use Settings > Windows Update > Check for updates.' -ForegroundColor Green
} else {
    Write-Host "$failures step(s) failed - see $LogFile. The saved state was kept; fix the cause and run this again." -ForegroundColor Yellow
}
Write-Host "Log: $LogFile"
exit $failures
