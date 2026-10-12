#Requires -Version 5.1
<#
.SYNOPSIS
  Register the devin-switch-drift scheduled task (user scope, no admin).

.DESCRIPTION
  Imports devin-switch-drift.task.xml: `devin-switch doctor` at logon and
  daily. Notification only — output is the task's last-run result; drift
  shows as "closest profile: <name> (N files away)".

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install-task.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = 'devin-switch-drift'
)

$ErrorActionPreference = 'Stop'

$xmlPath = Join-Path $PSScriptRoot 'devin-switch-drift.task.xml'
if (-not (Test-Path $xmlPath)) {
    throw "task XML not found next to this script: $xmlPath"
}

if (-not (Get-Command devin-switch -ErrorAction SilentlyContinue)) {
    Write-Warning "'devin-switch' is not on PATH for this session — " +
        "install it (uv tool install devin-switch) before enabling."
}

if ($PSCmdlet.ShouldProcess($TaskName, 'Register scheduled task')) {
    try {
        $xml = Get-Content -Raw -Path $xmlPath
        Register-ScheduledTask -TaskName $TaskName -Xml $xml -Force | Out-Null
    }
    catch {
        if ($_.Exception.HResult -eq -2147024891) {  # 0x80070005 E_ACCESSDENIED
            Write-Error ("Access denied registering '$TaskName'. " +
                "User-scope tasks normally need no admin — a policy is " +
                "blocking Task Scheduler. See README.corporate-windows.md.")
        }
        throw
    }
    Write-Host "Registered scheduled task '$TaskName' (user scope)."
    Write-Host "Smoke check: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
}
