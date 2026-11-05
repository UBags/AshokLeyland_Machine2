# powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\DisableAllUpdates.ps1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Disables all automatic updates on Windows 11.

.DESCRIPTION
    Run it from an elevated (Administrator) PowerShell, in the folder that holds this file,
    with the command on the first line.

    The script re-runs itself as SYSTEM through a one-off scheduled task, which it deletes
    afterwards. Administrators can't change the Windows Update Medic service or the Update
    Orchestrator tasks, so SYSTEM has to do it.

    What it turns off:
      - Windows Update: quality, feature and driver updates. It sets the policies and points
        the update source at an address that does not exist.
      - Services: Windows Update (wuauserv), Update Orchestrator (UsoSvc), Windows Update
        Medic (WaaSMedicSvc), Microsoft Update Health (uhssvc), Windows Remediation (sedsvc),
        Microsoft Edge Update (edgeupdate, edgeupdatem).
      - Scheduled tasks: WindowsUpdate, UpdateOrchestrator, WaaSMedic, rempl,
        InstallService\ScanForUpdates*, MicrosoftEdgeUpdateTask*.
      - Driver and device-metadata downloads from Windows Update.
      - Microsoft Store automatic app updates.
      - Microsoft Edge updates.

    Not touched: Microsoft Defender, Office, and other vendors' updaters.

    State and logs:
      - The state found before the first run is saved in C:\ProgramData\UpdateControl\state.json.
        EnableAllUpdates.ps1 restores it.
      - Running this script again keeps the state that the first run saved.
      - Log: C:\ProgramData\UpdateControl\DisableAllUpdates.log

    Restart the PC afterwards.

    If the PC is joined to a domain, a domain Group Policy can override these settings.
#>
param([switch]$AsSystem)

$ErrorActionPreference = 'Stop'

$ScriptName    = 'DisableAllUpdates'
$StateDir      = Join-Path $env:ProgramData 'UpdateControl'
$StateFile     = Join-Path $StateDir 'state.json'
$LogFile       = Join-Path $StateDir "$ScriptName.log"
$DoneFile      = Join-Path $StateDir "$ScriptName.done"
$WorkerFile    = Join-Path $StateDir "$ScriptName.worker.ps1"
$TaskName      = "UpdateControl-$ScriptName"
$BlockedServer = 'http://windows-update-blocked.invalid'   # .invalid never resolves

$WU = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate'
$Registry = @(
    @{ Path = $WU;      Name = 'DoNotConnectToWindowsUpdateInternetLocations'; Type = 'DWord';  Value = 1 }
    @{ Path = $WU;      Name = 'DisableWindowsUpdateAccess';                   Type = 'DWord';  Value = 1 }
    @{ Path = $WU;      Name = 'SetDisableUXWUAccess';                         Type = 'DWord';  Value = 1 }
    @{ Path = $WU;      Name = 'ExcludeWUDriversInQualityUpdate';              Type = 'DWord';  Value = 1 }
    @{ Path = $WU;      Name = 'WUServer';                                     Type = 'String'; Value = $BlockedServer }
    @{ Path = $WU;      Name = 'WUStatusServer';                               Type = 'String'; Value = $BlockedServer }
    @{ Path = "$WU\AU"; Name = 'NoAutoUpdate';                                 Type = 'DWord';  Value = 1 }
    @{ Path = "$WU\AU"; Name = 'UseWUServer';                                  Type = 'DWord';  Value = 1 }
    @{ Path = "$WU\AU"; Name = 'NoAutoRebootWithLoggedOnUsers';                Type = 'DWord';  Value = 1 }
    @{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\DriverSearching';
       Name = 'SearchOrderConfig';                Type = 'DWord'; Value = 0 }
    @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Device Metadata';
       Name = 'PreventDeviceMetadataFromNetwork'; Type = 'DWord'; Value = 1 }
    @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\WindowsStore';
       Name = 'AutoDownload';                     Type = 'DWord'; Value = 2 }
    @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\EdgeUpdate';
       Name = 'UpdateDefault';                    Type = 'DWord'; Value = 0 }
    @{ Path = 'HKLM:\SOFTWARE\Policies\Microsoft\EdgeUpdate';
       Name = 'AutoUpdateCheckPeriodMinutes';     Type = 'DWord'; Value = 0 }
)

$Services = @('wuauserv', 'UsoSvc', 'WaaSMedicSvc', 'uhssvc', 'sedsvc', 'edgeupdate', 'edgeupdatem')

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

    # 1. Record the current state (only what the first run has not recorded yet)
    $state = [ordered]@{ Created = (Get-Date).ToString('s'); Registry = @(); Services = @(); Tasks = @() }
    if (Test-Path $StateFile) {
        $old = Get-Content -Path $StateFile -Raw | ConvertFrom-Json
        $state.Created  = $old.Created
        $state.Registry = @($old.Registry | Where-Object { $_ })
        $state.Services = @($old.Services | Where-Object { $_ })
        $state.Tasks    = @($old.Tasks    | Where-Object { $_ })
        Write-Log "State saved on $($old.Created) kept (this is a repeat run)."
    }

    foreach ($r in $Registry) {
        if ($state.Registry | Where-Object { $_.Path -eq $r.Path -and $_.Name -eq $r.Name }) { continue }
        $entry = [ordered]@{ Path = $r.Path; Name = $r.Name; KeyExisted = (Test-Path $r.Path)
                             Existed = $false; Kind = $null; Value = $null }
        if ($entry.KeyExisted) {
            $key = Get-Item -Path $r.Path
            if ($key.GetValueNames() -contains $r.Name) {
                $entry.Existed = $true
                $entry.Kind    = $key.GetValueKind($r.Name).ToString()
                $entry.Value   = $key.GetValue($r.Name, $null, 'DoNotExpandEnvironmentNames')
            }
        }
        $state.Registry += [pscustomobject]$entry
    }

    foreach ($name in $Services) {
        if ($state.Services | Where-Object { $_.Name -eq $name }) { continue }
        $k = Get-ServiceKey $name
        if (Test-Path $k) {
            $state.Services += [pscustomobject]@{ Name = $name; Start = (Get-ItemProperty -Path $k).Start }
        }
    }

    foreach ($t in Get-UpdateTasks) {
        if ($state.Tasks | Where-Object { $_.Path -eq $t.TaskPath -and $_.Name -eq $t.TaskName }) { continue }
        $state.Tasks += [pscustomobject]@{ Path = $t.TaskPath; Name = $t.TaskName
                                           Enabled = ($t.State -ne 'Disabled') }
    }

    $state | ConvertTo-Json -Depth 5 | Set-Content -Path $StateFile -Encoding UTF8
    Write-Log "State saved to $StateFile"

    # 2. Policies and registry settings
    Write-Log '--- Policies and registry settings'
    foreach ($r in $Registry) {
        try {
            if (-not (Test-Path $r.Path)) { New-Item -Path $r.Path -Force | Out-Null }
            New-ItemProperty -Path $r.Path -Name $r.Name -PropertyType $r.Type -Value $r.Value -Force | Out-Null
            Write-Log ('OK      {0}\{1} = {2}' -f $r.Path, $r.Name, $r.Value)
        } catch {
            $failures++
            Write-Log ('FAILED  {0}\{1}: {2}' -f $r.Path, $r.Name, $_.Exception.Message)
        }
    }

    # 3. Services: startup type Disabled (Start = 4), then stop
    Write-Log '--- Services'
    foreach ($name in $Services) {
        $k = Get-ServiceKey $name
        if (-not (Test-Path $k)) { Write-Log "SKIPPED service $name (not installed)"; continue }
        try {
            Set-ItemProperty -Path $k -Name 'Start' -Value 4 -Type DWord
            $now = (Get-ItemProperty -Path $k).Start
            if ($now -ne 4) { throw "Start is $now after the write" }
        } catch {
            $failures++
            Write-Log "FAILED  service ${name}: $($_.Exception.Message)"
            continue
        }
        try {
            $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
            if ($svc -and $svc.Status -ne 'Stopped') { Stop-Service -Name $name -Force }
            Write-Log "OK      service $name disabled and stopped"
        } catch {
            Write-Log "OK      service $name disabled; could not stop it now ($($_.Exception.Message)); it stays off after the restart"
        }
    }

    # 4. Scheduled tasks
    Write-Log '--- Scheduled tasks'
    foreach ($t in Get-UpdateTasks) {
        $full = "$($t.TaskPath)$($t.TaskName)"
        try {
            if ($t.State -eq 'Running') { Stop-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName -ErrorAction SilentlyContinue }
            Disable-ScheduledTask -TaskPath $t.TaskPath -TaskName $t.TaskName | Out-Null
            Write-Log "OK      task $full disabled"
        } catch {
            $failures++
            Write-Log "FAILED  task ${full}: $($_.Exception.Message)"
        }
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
    Write-Host 'All updates disabled. Restart the PC.' -ForegroundColor Green
} else {
    Write-Host "$failures step(s) failed - see $LogFile. Restart the PC." -ForegroundColor Yellow
}
Write-Host "Log: $LogFile"
exit $failures
