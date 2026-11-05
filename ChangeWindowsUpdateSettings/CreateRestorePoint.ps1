# powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\CreateRestorePoint.ps1 -Install
#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Creates a System Restore point when the newest one is older than 72 hours, and installs
    itself as a scheduled task that runs at every start-up and every hour.

.DESCRIPTION
    Run from an elevated (Administrator) Windows PowerShell (powershell.exe, not pwsh):

      -Install    Copies this file to C:\ProgramData\RestorePoint\, turns on System Protection
                  for the system drive, and registers the scheduled task "CreateRestorePoint"
                  (runs as SYSTEM). The task's triggers:
                    - at every start-up, 5 minutes after boot;
                    - every hour.
                  Each run creates a restore point only if the newest one is older than
                  -MaxAgeHours (72). A run missed while the PC was off runs when it is back on.
                  Then it runs the check once.
      -Uninstall  Removes the scheduled task. Restore points already made are kept.
      -Force      Creates a restore point now, whatever the age of the newest one.
      (no switch) Checks the age of the newest restore point and creates one if it is too old
                  (this is what the task runs).

    Log: C:\ProgramData\RestorePoint\CreateRestorePoint.log (last 1000 lines kept).
#>
param(
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Force,
    [int]$MaxAgeHours    = 72,
    [int]$StoragePercent = 10      # disk space System Protection may use (set by -Install)
)

$ErrorActionPreference = 'Stop'

$Drive     = "$env:SystemDrive\"
$Dir       = Join-Path $env:ProgramData 'RestorePoint'
$Installed = Join-Path $Dir 'CreateRestorePoint.ps1'
$LogFile   = Join-Path $Dir 'CreateRestorePoint.log'
$TaskName  = 'CreateRestorePoint'
$SRKey     = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SystemRestore'
$SRPolicy  = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\SystemRestore'

function Write-Log([string]$Message, [string]$Level = 'INFO') {
    $line = '{0:yyyy-MM-dd HH:mm:ss}  {1,-7}  {2}' -f (Get-Date), $Level, $Message
    Write-Host $line
    try {
        Add-Content -Path $LogFile -Value $line -Encoding UTF8
        $lines = @(Get-Content -Path $LogFile)
        if ($lines.Count -gt 1100) { $lines[-1000..-1] | Set-Content -Path $LogFile -Encoding UTF8 }
    } catch { }
}

function Get-NewestRestorePointTime {
    $times = @(Get-ComputerRestorePoint -ErrorAction SilentlyContinue | ForEach-Object {
        [Management.ManagementDateTimeConverter]::ToDateTime($_.CreationTime)
    })
    if ($times.Count -eq 0) { return $null }
    return ($times | Sort-Object -Descending | Select-Object -First 1)
}

function Assert-Prerequisites {
    if ($PSVersionTable.PSEdition -ne 'Desktop') {
        throw 'Run with Windows PowerShell (powershell.exe); the restore point cmdlets are not in PowerShell 7.'
    }
    $policy = Get-ItemProperty -Path $SRPolicy -ErrorAction SilentlyContinue
    if ($policy -and $policy.DisableSR -eq 1) {
        throw "System Restore is turned off by Group Policy ($SRPolicy\DisableSR = 1)."
    }
    $vss = Get-Service -Name VSS -ErrorAction SilentlyContinue
    if (-not $vss -or $vss.StartType -eq 'Disabled') {
        throw 'The Volume Shadow Copy service (VSS) is missing or disabled; restore points need it.'
    }
}

function Enable-Protection {
    # Idempotent: turns System Protection on for the system drive if it is off.
    Enable-ComputerRestore -Drive $Drive
    # Windows skips a new restore point if one was made in the last 24 h (default 1440 min).
    # 0 = no minimum gap; this script sets its own (MaxAgeHours).
    if (-not (Test-Path $SRKey)) { New-Item -Path $SRKey -Force | Out-Null }
    New-ItemProperty -Path $SRKey -Name 'SystemRestorePointCreationFrequency' -PropertyType DWord -Value 0 -Force | Out-Null
}

function New-RestorePoint {
    $before = Get-Date
    $desc   = 'Automatic {0:yyyy-MM-dd HH:mm}' -f $before
    Write-Log "Creating restore point '$desc' on $Drive ..."
    # RestorePointType 12 = MODIFY_SETTINGS, EventType 100 = BEGIN_SYSTEM_CHANGE
    $result = Invoke-CimMethod -Namespace 'root/default' -ClassName 'SystemRestore' -MethodName 'CreateRestorePoint' `
                -Arguments @{ Description = $desc; RestorePointType = [uint32]12; EventType = [uint32]100 }
    if ($result.ReturnValue -ne 0) {
        throw ('CreateRestorePoint returned {0} (0x{0:X8})' -f $result.ReturnValue)
    }
    $newest = Get-NewestRestorePointTime
    if ($newest -and $newest -ge $before.AddMinutes(-1)) {
        Write-Log ('Restore point created ({0:yyyy-MM-dd HH:mm:ss}).' -f $newest) 'OK'
    } else {
        throw 'CreateRestorePoint returned 0 but no new restore point is listed (is System Protection on, with disk space allotted?).'
    }
}

function Invoke-Check {
    Assert-Prerequisites
    Enable-Protection
    $newest = Get-NewestRestorePointTime
    if ($Force) {
        Write-Log 'Forced run.'
        New-RestorePoint
        return
    }
    if (-not $newest) {
        Write-Log 'No restore point exists.'
        New-RestorePoint
        return
    }
    $age = (Get-Date) - $newest
    if ($age.TotalHours -ge $MaxAgeHours) {
        Write-Log ('Newest restore point {0:yyyy-MM-dd HH:mm} is {1:N1} h old (limit {2} h).' -f $newest, $age.TotalHours, $MaxAgeHours)
        New-RestorePoint
    } else {
        Write-Log ('Newest restore point {0:yyyy-MM-dd HH:mm} is {1:N1} h old; next one after {2:yyyy-MM-dd HH:mm}.' -f `
                   $newest, $age.TotalHours, $newest.AddHours($MaxAgeHours)) 'SKIP'
    }
}

# ----------------------------------------------------------------------------------------
New-Item -ItemType Directory -Path $Dir -Force | Out-Null

try {
    if ($Uninstall) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Write-Log "Scheduled task '$TaskName' removed." 'OK'
        exit 0
    }

    if ($Install) {
        Assert-Prerequisites
        if ($PSCommandPath -ne $Installed) { Copy-Item -LiteralPath $PSCommandPath -Destination $Installed -Force }
        Enable-Protection
        $vss = & vssadmin.exe Resize ShadowStorage "/For=$env:SystemDrive" "/On=$env:SystemDrive" "/MaxSize=$StoragePercent%" 2>&1
        Write-Log ("Shadow storage {0}%: {1}" -f $StoragePercent, (($vss | Where-Object { $_ -match '\S' }) -join ' '))

        $psExe   = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $action  = New-ScheduledTaskAction -Execute $psExe `
                     -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Installed`" -MaxAgeHours $MaxAgeHours"
        $atBoot  = New-ScheduledTaskTrigger -AtStartup
        $atBoot.Delay = 'PT5M'
        $hourly  = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddHours((Get-Date).Hour + 1) -RepetitionInterval (New-TimeSpan -Hours 1)
        $princ   = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        $set     = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                     -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 1)
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atBoot, $hourly) -Principal $princ `
            -Settings $set -Description "Creates a System Restore point when the newest is older than $MaxAgeHours h." -Force | Out-Null
        Write-Log "Scheduled task '$TaskName' registered (at start-up + 5 min, and hourly; as SYSTEM; runs $Installed)." 'OK'
    }

    Invoke-Check
    exit 0
} catch {
    Write-Log $_.Exception.Message 'ERROR'
    exit 1
}
