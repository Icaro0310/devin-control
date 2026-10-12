#Requires -Version 5.1
<#
.SYNOPSIS
  Remove the devin-switch-drift scheduled task (user scope, no admin).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\remove-task.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = 'devin-switch-drift'
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
    Write-Host "Removed scheduled task '$TaskName'."
}
