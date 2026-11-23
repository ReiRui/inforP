param(
    [switch]$DryRun
)

# ASCII-safe on purpose. The scheduled task reads this file as UTF-8 before executing it.
# Runs the local inforP pipeline (DeepSeek) instead of the Codex CLI.
#
# Schedule: every weekday at 09:00.
#   - always generates the daily report
#   - on Monday also generates the weekly report
#
# Delivery is decided by .env (DELIVERY_CHANNELS / EMAIL_RECIPIENT_SET).
# WeCom is intentionally disabled in .env at the moment; this script never calls it.

$ErrorActionPreference = "Stop"

# When the task loader runs this file as a ScriptBlock (the no-pwsh fallback), $PSScriptRoot
# is empty. In that case the loader has already Set-Location'd to the project root.
$Project = if ($PSScriptRoot) {
    (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
    (Get-Location).Path
}
$Inforp = Join-Path $Project ".venv\Scripts\inforp.exe"
$LogDir = Join-Path $Project "logs"
$RunLog = Join-Path $LogDir "pipeline_daily_runs.jsonl"
$Today = Get-Date -Format "yyyy-MM-dd"
$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$StartedAt = Get-Date
$IsMonday = $StartedAt.DayOfWeek -eq "Monday"

$Jobs = @("daily")
if ($IsMonday) {
    $Jobs += "weekly"
}

function Get-TargetPath([string]$job) {
    if ($job -eq "weekly") {
        return (Join-Path $Project "reports\$Today`_weekly.md")
    }
    return (Join-Path $Project "reports\$Today`_daily.md")
}

function Test-AlreadyDone([string]$job, [string]$date) {
    if (-not (Test-Path -LiteralPath $RunLog)) { return $false }
    foreach ($line in Get-Content -LiteralPath $RunLog -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed) { continue }
        try { $record = $trimmed | ConvertFrom-Json } catch { continue }
        if ($record.date -eq $date -and $record.job -eq $job -and $record.ok -eq $true) {
            return $true
        }
    }
    return $false
}

function Write-RunRecord($record) {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    ($record | ConvertTo-Json -Compress) | Add-Content -LiteralPath $RunLog -Encoding UTF8
}

function Write-Incident([string]$title, [string]$detail, [string]$component) {
    # Best effort: an incident that cannot be logged must not mask the original failure.
    try {
        & $Inforp log-incident $title $detail --component $component --severity error --status open | Out-Null
    } catch {
        Write-Output "WARN: could not log incident: $($_.Exception.Message)"
    }
}

if ($DryRun) {
    "DRYRUN project=$Project"
    "DRYRUN today=$Today monday=$IsMonday jobs=$($Jobs -join ',')"
    foreach ($job in $Jobs) {
        "DRYRUN would run: $Inforp run-$job"
        "DRYRUN expect report: $(Get-TargetPath $job)"
        "DRYRUN already done: $(Test-AlreadyDone $job $Today)"
    }
    "DRYRUN wecom: disabled by .env (WECOM_WEEKLY_SUMMARY_ENABLED=false)"
    exit 0
}

Set-Location -LiteralPath $Project
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Failed = @()

foreach ($Job in $Jobs) {
    $Target = Get-TargetPath $Job

    if (Test-AlreadyDone $Job $Today) {
        Write-Output "skip $Job : a successful run for $Today is already recorded"
        continue
    }

    $SkipTag = "pipeline_$($Job)_$Stamp"
    $Watch = [System.Diagnostics.Stopwatch]::StartNew()
    $Output = $null
    $ExitCode = 1

    try {
        $Output = & $Inforp "run-$Job" 2>&1 | Out-String
        if ($LASTEXITCODE -is [int]) { $ExitCode = $LASTEXITCODE } else { $ExitCode = 0 }
    } catch {
        $Output = "EXCEPTION: $($_.Exception.Message)"
        $ExitCode = 1
    }
    $Watch.Stop()
    $Seconds = [int]$Watch.Elapsed.TotalSeconds

    $ReportExists = Test-Path -LiteralPath $Target
    $Ok = ($ExitCode -eq 0) -and $ReportExists

    $Tail = ""
    if ($Output) {
        $Tail = $Output.Trim()
        if ($Tail.Length -gt 2000) { $Tail = $Tail.Substring($Tail.Length - 2000) }
    }

    $Record = [ordered]@{
        method = "windows_task_pipeline"
        job = $Job
        date = $Today
        ok = $Ok
        exit_code = $ExitCode
        seconds = $Seconds
        timestamp = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
        report_path = if ($ReportExists) { $Target } else { $null }
        skip_tag = $SkipTag
        wecom_result = $null
        output_tail = $Tail
    }
    Write-RunRecord $Record

    if (-not $Ok) {
        $Failed += $Job
        $Detail = "inforp run-$Job exited $ExitCode after $Seconds seconds; report_exists=$ReportExists; report=$Target; tail=$Tail"
        Write-Incident "Pipeline $Job run failed" $Detail "automation.pipeline"
        Write-Output "FAIL $Job exit=$ExitCode report=$ReportExists"
    } else {
        Write-Output "OK $Job exit=$ExitCode report=$ReportExists seconds=$Seconds"
    }
}

if ($Failed.Count -gt 0) {
    exit 1
}
exit 0
