#Requires -Version 5.1
<#
.SYNOPSIS
  Remove the devin-office scheduled task (user scope, no admin).

.DESCRIPTION
  Unregisters the at-logon task that keeps the devin-office dashboard
  daemon running. Any daemon.py process already running is stopped by
  Task Scheduler when the task ends; the package checkout is untouched.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\remove-task.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = 'devin-office'
)

$ErrorActionPreference = 'Stop'

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Scheduled task '$TaskName' is not registered — nothing to do."
    return
}

if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister scheduled task')) {
    try {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }
    catch {
        if ($_.Exception.HResult -eq -2147024891) {  # 0x80070005 E_ACCESSDENIED
            Write-Error ("Access denied removing '$TaskName'. " +
                "See README.corporate-windows.md.")
        }
        throw
    }
    Write-Host "Removed scheduled task '$TaskName'. The devin-control checkout is left in place."
}
