#Requires -Version 5.1
<#
.SYNOPSIS
  Register the devin-bridge-intake scheduled task (user scope, no admin).

.DESCRIPTION
  Imports devin-bridge-intake.task.xml into Task Scheduler under the current
  user. The task fires at logon and repeats every 5 minutes, draining the
  bridge mailbox inbox via `devin-bridge intake`.

  User-scope tasks need no elevation. If you get access denied
  (0x80070005), your account or policy blocks Task Scheduler — see
  README.corporate-windows.md.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install-task.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = 'devin-bridge-intake'
)

$ErrorActionPreference = 'Stop'

$xmlPath = Join-Path $PSScriptRoot 'devin-bridge-intake.task.xml'
if (-not (Test-Path $xmlPath)) {
    throw "task XML not found next to this script: $xmlPath"
}

if (-not (Get-Command devin-bridge -ErrorAction SilentlyContinue)) {
    Write-Warning ("'devin-bridge' is not on PATH for this session — " +
        "the task will fail until npm's global bin dir is on PATH " +
        "(npm config get prefix).")
}

if ($PSCmdlet.ShouldProcess($TaskName, 'Register scheduled task')) {
    try {
        $xml = Get-Content -Raw -Path $xmlPath
        Register-ScheduledTask -TaskName $TaskName -Xml $xml -Force | Out-Null
    }
    catch {
        if ($_.Exception.HResult -eq -2147024891) {  # 0x80070005 E_ACCESSDENIED
            Write-Error ("Access denied registering '$TaskName'. " +
                "User-scope tasks normally need no admin — this means a " +
                "policy blocks Task Scheduler or your account lacks " +
                "'Log on as a batch job'. See README.corporate-windows.md.")
        }
        throw
    }
    Write-Host "Registered scheduled task '$TaskName' (user scope)."
    Write-Host "Smoke check: Get-ScheduledTask -TaskName '$TaskName' | Get-ScheduledTaskInfo"
}
