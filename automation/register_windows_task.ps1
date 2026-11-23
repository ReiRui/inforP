$ErrorActionPreference = "Stop"

# Registers the weekday 09:00 task.
#
# The action runs automation\run_pipeline_daily.ps1 (local DeepSeek pipeline).
# Re-running this script replaces the existing task in place.

$TaskName = "inforP-DailyWeekly"
$Project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Script = Join-Path $Project "automation\run_pipeline_daily.ps1"
$Pwsh = Get-Command pwsh -ErrorAction SilentlyContinue

if ($Pwsh) {
  $Execute = $Pwsh.Source
  $Argument = ('-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $Script)
} else {
  $Execute = "powershell.exe"
  $Loader = @"
`$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath '$Project'
`$scriptPath = '$Script'
`$scriptText = Get-Content -LiteralPath `$scriptPath -Raw -Encoding UTF8
`$scriptBlock = [ScriptBlock]::Create(`$scriptText)
& `$scriptBlock
if (`$LASTEXITCODE -is [int]) { exit `$LASTEXITCODE }
"@
  $Encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($Loader))
  $Argument = ('-NoProfile -ExecutionPolicy Bypass -EncodedCommand {0}' -f $Encoded)
}

$Action = New-ScheduledTaskAction -Execute $Execute -Argument $Argument -WorkingDirectory $Project
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 9:00am
$Settings = New-ScheduledTaskSettingsSet `
  -Compatibility Win8 `
  -StartWhenAvailable `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -WakeToRun `
  -ExecutionTimeLimit (New-TimeSpan -Hours 3)

Register-ScheduledTask `
  -TaskName $TaskName `
  -Action $Action `
  -Trigger $Trigger `
  -Settings $Settings `
  -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName $TaskName | Select-Object NextRunTime, LastRunTime, LastTaskResult
