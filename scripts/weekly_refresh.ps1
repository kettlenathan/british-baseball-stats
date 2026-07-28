<#
.SYNOPSIS
    Weekly scrape + publish, run locally by Windows Task Scheduler.

.DESCRIPTION
    This is the scheduled replacement for the GitHub Actions cron job. The
    Action was moved to manual-only because stats.britishbaseball.org.uk's
    CloudFront/WAF returns 403 to GitHub's hosted-runner IP ranges: every
    scheduled run since 2026-07-19 failed on the very first schedule fetch,
    while the identical request from a normal home connection returns 200.
    Running the scrape from this machine's own IP is what makes it work.

    Does exactly what the workflow did, in the same order:
      1. scripts.refresh_data  (scrape the window, then recompute derived stats)
      2. scripts.publish_db    (upload data/stats.db as the data-latest release asset)

    Step 2 only runs if step 1 succeeded, so a blocked or broken scrape never
    republishes a database that nothing was added to. refresh_data itself
    exits non-zero when every league's schedule fetch fails, so a total block
    is a visible failure rather than a green no-op.

    Deliberately does NOT pull the published DB first (unlike the workflow,
    which started from a bare checkout): the local data/stats.db is the
    working copy here, and overwriting it would discard any local scrape or
    recompute work that hasn't been published yet. Use scripts.pull_latest_db
    explicitly if you do want to reset to the published snapshot.

.PARAMETER Years
    Season(s) to refresh, in scripts.refresh_data's format. Defaults to the
    current calendar year. Note this means the task will fail every week
    during the off-season, once the new year ticks over but before the new
    season's fixtures are published -- disable the task or pass -Years
    explicitly if that noise is unwanted.

.PARAMETER Window
    Box-score fetch window: LastWeek (default, matches the weekly cadence),
    LastMonth (good for a catch-up after missed weeks), or Full (every final
    game in the season -- slow, needed only after adding derived fields).

.EXAMPLE
    # What the scheduled task runs:
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\weekly_refresh.ps1

.EXAMPLE
    # Catch up after a few missed weeks:
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\weekly_refresh.ps1 -Window LastMonth
#>
[CmdletBinding()]
param(
    [string] $Leagues = 'nbl,d2,d3,d4,d5',
    [string] $Years = (Get-Date).Year.ToString(),
    [ValidateSet('LastWeek', 'LastMonth', 'Full')]
    [string] $Window = 'LastWeek',
    [switch] $SkipPublish
)

$ErrorActionPreference = 'Stop'

# Inherited by the python child processes. Without it Python block-buffers
# stdout when it isn't attached to a console (which it never is here), so the
# log stays empty for the whole run and any output still in the buffer is lost
# if the task hits its execution time limit.
$env:PYTHONUNBUFFERED = '1'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $RepoRoot 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }
$LogFile = Join-Path $LogDir ("refresh-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd-HHmmss'))

function Write-Log {
    param([string] $Message)
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

# Task Scheduler's S4U logon type gives the task a minimal PATH that often
# lacks ~\.local\bin, so uv is resolved explicitly rather than assumed.
function Resolve-Uv {
    $candidates = @(
        (Join-Path $env:USERPROFILE '.local\bin\uv.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\uv\uv.exe')
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    $cmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    throw "Could not locate uv.exe. Install uv or add it to PATH."
}

# Start-Process with redirected streams rather than `uv ... 2>&1 | Tee-Object`:
# in Windows PowerShell 5.1, redirecting a native executable's stderr inside
# the pipeline wraps each line in a NativeCommandError and makes a
# zero-exit-code run look like a failure. Separate files, merged after, keep
# the exit code trustworthy.
function Invoke-Step {
    param([string] $Name, [string] $Uv, [string[]] $Arguments)

    Write-Log "=== $Name : $Uv $($Arguments -join ' ')"
    # These live beside the log rather than in %TEMP% so a run in progress is
    # inspectable: Start-Process writes them as the child produces output,
    # whereas the merge into $LogFile below only happens once the step ends,
    # and a full scrape takes tens of minutes.
    $outFile = "$LogFile.$Name.out"
    $errFile = "$LogFile.$Name.err"
    try {
        $proc = Start-Process -FilePath $Uv -ArgumentList $Arguments `
            -WorkingDirectory $RepoRoot -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        foreach ($f in @($outFile, $errFile)) {
            if ((Get-Item $f).Length -gt 0) {
                Add-Content -Path $LogFile -Value (Get-Content $f -Raw) -Encoding utf8
            }
        }
        Write-Log "=== $Name finished with exit code $($proc.ExitCode)"
        return $proc.ExitCode
    }
    finally {
        Remove-Item $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

# A scheduled task that fails is otherwise completely silent -- which is how
# the GitHub Actions cron managed to report green while scraping nothing for
# two weeks. Best-effort only: this shows nothing when the task runs with no
# interactive desktop (the S4U principal, session 0), so it supplements rather
# than replaces the exit code and the log.
function Show-FailureNotice {
    param([string] $Message)
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        $icon = New-Object System.Windows.Forms.NotifyIcon
        $icon.Icon = [System.Drawing.SystemIcons]::Warning
        $icon.Visible = $true
        $icon.ShowBalloonTip(20000, 'British Baseball Stats refresh failed', $Message,
                             [System.Windows.Forms.ToolTipIcon]::Error)
        Start-Sleep -Seconds 12
        $icon.Dispose()
    }
    catch {
        Write-Log "(could not show desktop notification: $($_.Exception.Message))"
    }
}

$windowArgs = switch ($Window) {
    'LastWeek'  { @('--last-week') }
    'LastMonth' { @('--last-month') }
    'Full'      { @() }
}

Write-Log "Weekly refresh starting (leagues=$Leagues years=$Years window=$Window)"
$uv = Resolve-Uv
Write-Log "Using uv at $uv"

$refreshArgs = @('run', 'python', '-m', 'scripts.refresh_data',
                 '--leagues', $Leagues, '--years', $Years) + $windowArgs
$code = Invoke-Step -Name 'refresh_data' -Uv $uv -Arguments $refreshArgs
if ($code -ne 0) {
    Write-Log "Refresh failed (exit $code) -- NOT publishing. See the log above."
    Write-Host "Refresh failed. Log: $LogFile"
    Show-FailureNotice "Scrape/recompute failed (exit $code). Nothing was published. See $LogFile"
    exit $code
}

if ($SkipPublish) {
    Write-Log "Refresh succeeded; -SkipPublish set, so not publishing."
    exit 0
}

# publish_db reads GITHUB_TOKEN from the environment; config.py's load_dotenv
# puts the .env value there on import, so no token handling is needed here.
$code = Invoke-Step -Name 'publish_db' -Uv $uv -Arguments @('run', 'python', '-m', 'scripts.publish_db')
if ($code -ne 0) {
    Write-Log "Publish failed (exit $code). The local data/stats.db is still up to date -- re-run scripts.publish_db once the cause is fixed."
    Write-Host "Publish failed. Log: $LogFile"
    Show-FailureNotice "Scrape succeeded but publishing to the GitHub Release failed (exit $code). The live app still shows old data. See $LogFile"
    exit $code
}

Write-Log "Weekly refresh completed successfully."

# Keep the last ~3 months of logs so a silently-failing task is diagnosable
# without the directory growing forever.
Get-ChildItem $LogDir -Filter 'refresh-*.log' |
    Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-90) } |
    Remove-Item -Force -ErrorAction SilentlyContinue

Write-Host "Refresh complete. Log: $LogFile"
exit 0
