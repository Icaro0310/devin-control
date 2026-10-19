#Requires -Version 5.1
<#
.SYNOPSIS
  Register the devin-office scheduled task (user scope, no admin).

.DESCRIPTION
  Imports devin-office.task.xml into Task Scheduler under the current
  user. The task fires at logon and keeps `py -3 daemon.py --port 8788`
  running for the session — the dashboard on http://localhost:8788.

  The XML carries a __OFFICE_CHECKOUT__ placeholder because daemon.py
  lives in the package checkout (under src\devin_office\ — a bare checkout
  needs no install; `uv tool install`/`pip install -e` users can point the
  action at the `devin-office` CLI instead); this script resolves the
  checkout from its own location and patches the XML before registering.

  User-scope tasks need no elevation. If you get access denied
  (0x80070005), your account or policy blocks Task Scheduler — see
  README.corporate-windows.md.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install-task.ps1
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$TaskName = 'devin-office'
)

$ErrorActionPreference = 'Stop'

$xmlPath = Join-Path $PSScriptRoot 'devin-office.task.xml'
if (-not (Test-Path $xmlPath)) {
    throw "task XML not found next to this script: $xmlPath"
}

# This script sits at packages/office/adapters/daemon — the package
# root (where src\devin_office\daemon.py lives) is two levels up.
$officeDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$daemonPy = Join-Path $officeDir 'src\devin_office\daemon.py'
if (-not (Test-Path $daemonPy)) {
    throw "daemon.py not found at $daemonPy — is this script inside a devin-control checkout?"
}

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    Write-Warning ("'py' launcher is not on PATH for this session — " +
        "install Python 3 for Windows first (python.org) or the task " +
        "will fail at logon.")
}

if ($PSCmdlet.ShouldProcess($TaskName, 'Register scheduled task')) {
    try {
        # Patch the checkout path into the XML — literal replace (Windows
        # paths contain backslashes, so no -replace/regex) with an XML
        # escape first: a checkout/user dir containing '&', '<' or '>'
        # would otherwise produce malformed XML that registration rejects.
        $escapedDir = [System.Security.SecurityElement]::Escape($officeDir)
        $xml = (Get-Content -Raw -Path $xmlPath).Replace('__OFFICE_CHECKOUT__', $escapedDir)
        Register-ScheduledTask -TaskName $TaskName -Xml $xml -Force | Out-Null
    }
    catch {
        if ($_.Exception.HResult -eq -2147024891) {  # 0x80070005 E_ACCESSDENIED
            Write-Error ("Access denied registering '$TaskName'. " +
                "User-scope tasks normally need no admin — a policy is " +
                "blocking Task Scheduler or 'Log on as a batch job' was " +
                "revoked. See README.corporate-windows.md.")
        }
        throw
    }
    Write-Host "Registered scheduled task '$TaskName' (user scope, at-logon)."
    Write-Host "Smoke check: Start-ScheduledTask -TaskName '$TaskName'; then open http://localhost:8788"
}
