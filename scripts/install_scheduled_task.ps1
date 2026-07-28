<#
.SYNOPSIS
    Registers (or re-registers) the weekly data refresh as a Windows
    Scheduled Task. Run once; re-run after moving the repo or changing the
    schedule.

.DESCRIPTION
    Creates a task that runs scripts\weekly_refresh.ps1 every Sunday at 21:00
    local time. Because this machine's timezone is UK, that is 21:00 UK
    year-round -- unlike the old GitHub Actions cron ('0 20 * * 0' UTC), which
    was correct in BST but an hour early in GMT.

    The PC does not need to be on 24/7. The task settings handle the gaps:

      -WakeToRun            wakes the machine from sleep/hibernate to run.
                            Cannot wake from a full shutdown -- that needs a
                            BIOS wake timer or Wake-on-LAN.
      -StartWhenAvailable   if the machine was off at 21:00, the task runs at
                            the next opportunity (boot/logon) instead of being
                            skipped. No time limit on the catch-up, and the
                            scrape pipeline is idempotent, so a Tuesday
                            catch-up ingests exactly the same games.
      -DontStopIfGoingOnBatteries / -AllowStartIfOnBatteries
                            a laptop on battery still refreshes.

    Missing a week is not a data gap either way: --last-week windows box-score
    fetching by game date, and the following week's run re-covers anything the
    site had not marked final yet.

    Runs as the current user. The logon type depends on whether this script
    was started elevated, because registering an S4U task requires admin:

      elevated    -> S4U: runs whether the user is logged on or not, without
                     storing a password. Covers the case where the machine is
                     booted but sitting at the lock screen with nobody
                     logged in.
      unelevated  -> Interactive: runs only while this user is logged on.
                     Combined with -StartWhenAvailable that is very nearly as
                     good -- a machine woken from sleep still has the session,
                     and after a cold boot the task fires at logon instead.

    Unelevated is the default because it needs no UAC prompt; re-run this
    script from an elevated PowerShell if you want the S4U behaviour.

.PARAMETER TaskName
    Name to register under. Default 'British Baseball Stats Weekly Refresh'.

.PARAMETER At
    Time of day to run. Default 21:00.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_scheduled_task.ps1

.EXAMPLE
    # Verify it works without waiting for Sunday:
    Start-ScheduledTask -TaskName 'British Baseball Stats Weekly Refresh'
    Get-ScheduledTaskInfo -TaskName 'British Baseball Stats Weekly Refresh'
#>
[CmdletBinding()]
param(
    [string] $TaskName = 'British Baseball Stats Weekly Refresh',
    [datetime] $At = '21:00',
    [ValidateSet('Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday')]
    [string] $DayOfWeek = 'Sunday'
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ScriptPath = Join-Path $RepoRoot 'scripts\weekly_refresh.ps1'
if (-not (Test-Path $ScriptPath)) { throw "Not found: $ScriptPath" }

$action = New-ScheduledTaskAction `
    -Execute 'powershell.exe' `
    -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $ScriptPath) `
    -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $DayOfWeek -At $At

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 3) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 30)

# S4U ("run whether the user is logged on or not") can only be registered by
# an administrator -- Register-ScheduledTask returns a bare "Access is denied"
# otherwise. Fall back to Interactive so the common, unelevated install still
# works; see this script's .DESCRIPTION for what that costs.
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$isElevated = (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
$logonType = if ($isElevated) { 'S4U' } else { 'Interactive' }

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType $logonType `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description 'Scrapes stats.britishbaseball.org.uk for the past week, recomputes derived stats, and publishes data/stats.db to the data-latest GitHub Release. Replaces the GitHub Actions cron, whose runner IPs the site blocks with 403.' `
    -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName"
Write-Host "  Runs:   $DayOfWeek at $($At.ToString('HH:mm')) local ($([System.TimeZoneInfo]::Local.Id))"
if ($isElevated) {
    Write-Host "  As:     $env:USERNAME (S4U -- runs whether logged on or not)"
} else {
    Write-Host "  As:     $env:USERNAME (Interactive -- runs while logged on; re-run elevated for S4U)"
}
Write-Host "  Script: $ScriptPath"
Write-Host "  Logs:   $(Join-Path $RepoRoot 'logs')"
Write-Host ""
Write-Host "Run it now to verify:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Check last result:     Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host "Remove it:             Unregister-ScheduledTask -TaskName '$TaskName'"
