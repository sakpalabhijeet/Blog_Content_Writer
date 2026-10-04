# Registers Windows scheduled tasks from the schedules in .env (BLOG_SCHEDULE, YT_SCHEDULE, POLL_MINUTES) -
# the same settings the Docker `serve` loop uses. Re-run this script after editing them.
#   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -Remove
# Run the agents on ONE machine only (this laptop or the Docker GPU laptop), not both.
param([switch]$Remove)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "venv\Scripts\python.exe"
$Tasks = @{ blog = "ContentAgents-Blog"; youtube = "ContentAgents-YouTube"; poll = "ContentAgents-Poll" }

function Remove-AgentTask([string]$Name) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
}

if ($Remove) {
    foreach ($n in $Tasks.Values) { Remove-AgentTask $n }
    Write-Host "Removed scheduled tasks."
    return
}
if (-not (Test-Path $Python)) { throw "venv not found at $Python" }

# Parse .env schedules with the app's own parser (single source of truth).
Push-Location $Root
try {
    $json = & $Python -m content_agents schedule-info
    if ($LASTEXITCODE -ne 0) { throw "Invalid schedule in .env (see the error above)." }
} finally { Pop-Location }
$info = $json | ConvertFrom-Json

function New-AgentAction([string]$Command) {
    New-ScheduledTaskAction -Execute $Python -Argument "-m content_agents $Command" -WorkingDirectory $Root
}

# Wake the laptop to run; catch up if it was off; don't stop long local-LLM jobs; never overlap.
$Settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 4) -MultipleInstances IgnoreNew

function Register-AgentJob([string]$Agent, $Schedule, [string]$Description) {
    $name = $Tasks[$Agent]
    if ($null -eq $Schedule) {
        Remove-AgentTask $name
        Write-Host ("{0,-24} off" -f $name)
        return
    }
    $at = [datetime]::ParseExact($Schedule.time, "HH:mm", $null)
    if ($null -eq $Schedule.days) {
        $trigger = New-ScheduledTaskTrigger -Daily -At $at
        $when = "daily at $($Schedule.time)"
    } else {
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Schedule.days -At $at
        $when = "$($Schedule.days -join ', ') at $($Schedule.time)"
    }
    # --scheduled: skip if this agent already started a run today (manual start or another scheduler).
    Register-ScheduledTask -TaskName $name -Action (New-AgentAction "start-$Agent --scheduled") -Trigger $trigger `
        -Settings $Settings -Description $Description -Force | Out-Null
    Write-Host ("{0,-24} {1}" -f $name, $when)
}

Register-AgentJob "blog" $info.blog "Weekly AI research + email blog topics"
Register-AgentJob "youtube" $info.youtube "Daily curious-topic research + email YouTube ideas"

$poll = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Minutes $info.poll_minutes) -RepetitionDuration (New-TimeSpan -Days 3650)
Register-ScheduledTask -TaskName $Tasks.poll -Action (New-AgentAction "poll") -Trigger $poll -Settings $Settings `
    -Description "Check Gmail for approval replies and continue runs" -Force | Out-Null
Write-Host ("{0,-24} every {1} min" -f $Tasks.poll, $info.poll_minutes)

Write-Host "`nSchedules come from .env - re-run this script after changing BLOG_SCHEDULE / YT_SCHEDULE / POLL_MINUTES."
Write-Host "Logs: $Root\data\logs\agents.log"
