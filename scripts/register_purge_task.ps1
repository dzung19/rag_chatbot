<#
.SYNOPSIS
  Đăng ký Task Scheduler chạy purge hằng ngày. Chạy bằng PowerShell Administrator.
  Chỉ đăng ký sau khi đã chạy run_purge.ps1 thủ công thành công vài lần.
#>
param(
    [int]$RetentionDays = 30,
    [string]$At = "2:00AM"
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$RunScript   = Join-Path $ProjectRoot "scripts\run_purge.ps1"
$TaskName    = "RAG Chatbot - Purge Chat History"

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`" -RetentionDays $RetentionDays" `
    -WorkingDirectory $ProjectRoot

$trigger  = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Backup and purge soft-deleted conversations past retention." `
    -Force

Write-Host "Registered '$TaskName' daily at $At (RetentionDays=$RetentionDays)."
Write-Host "Check: Get-ScheduledTaskInfo -TaskName '$TaskName'"
