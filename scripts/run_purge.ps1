<#
.SYNOPSIS
  Backup và xóa thật các conversation đã xóa mềm quá thời hạn lưu trữ.
  Đặt RetentionDays và BackupKeepDays theo quy định lưu trữ được phê duyệt.
#>
param(
    [int]$RetentionDays = 30,
    [int]$BackupKeepDays = 7,
    [int]$MaxDelete = 1000,
    [int]$LogKeepDays = 90
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$DataDir     = Join-Path $ProjectRoot "data"
$LogDir      = Join-Path $ProjectRoot "logs\purge"
$LockFile    = Join-Path $DataDir "purge.lock"
$PythonExe   = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$PurgeScript = Join-Path $ProjectRoot "scripts\purge_conversations.py"

New-Item -ItemType Directory -Force $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("purge_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))

function Write-Log([string]$Message) {
    $line = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

# 1. Chống chạy trùng
if (Test-Path $LockFile) {
    Write-Log "ABORT: another purge is running ($LockFile exists)."
    exit 10
}
New-Item -ItemType File $LockFile | Out-Null

$exitCode = 0
try {
    $env:PYTHONPATH = $ProjectRoot
    $env:PYTHONIOENCODING = "utf-8"
    if (-not $env:CONVERSATION_DATABASE_URL) {
        $env:CONVERSATION_DATABASE_URL = "sqlite:///" + (($DataDir -replace '\\', '/') + "/chat_history.db")
    }

    Write-Log "Start purge. RetentionDays=$RetentionDays MaxDelete=$MaxDelete"

    # 2. Backup + purge
    & $PythonExe $PurgeScript `
        --retention-days $RetentionDays `
        --max-delete $MaxDelete `
        --execute --yes 2>&1 | ForEach-Object { Write-Log "$_" }

    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "Purge script failed with exit code $exitCode"
    }

    # 3. Dọn backup quá hạn (backup cũng chứa dữ liệu đã xóa)
    Get-ChildItem $DataDir -Filter "chat_history_backup_*.db" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$BackupKeepDays) } |
        ForEach-Object {
            Remove-Item $_.FullName -Force
            Write-Log "Removed old backup: $($_.Name)"
        }

    # 4. Dọn log cũ
    Get-ChildItem $LogDir -Filter "purge_*.log" |
        Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-$LogKeepDays) } |
        Remove-Item -Force

    Write-Log "Purge completed successfully."
}
catch {
    Write-Log "FAILED: $_"
    if ($exitCode -eq 0) { $exitCode = 1 }
}
finally {
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
}

exit $exitCode
