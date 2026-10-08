# =============================================================================
# RAG Chatbot - Local Non-Docker Startup Script (Windows PowerShell)
# =============================================================================

param(
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"

# Resolve project root from this script location.
$ProjectRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
Set-Location $ProjectRoot

$PythonExe = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$ChromaExe = Join-Path $ProjectRoot "venv\Scripts\chroma.exe"
$EnvFile = Join-Path $ProjectRoot ".env"

# Runtime directories must not contain .env files. This prevents Chroma's
# Settings class from parsing the chatbot's application-only settings.
$RuntimeRoot = Join-Path $ProjectRoot ".runtime"
$ChromaRuntime = Join-Path $RuntimeRoot "chromadb"
$IngestionRuntime = Join-Path $RuntimeRoot "ingestion"
$RagRuntime = Join-Path $RuntimeRoot "rag"
$GatewayRuntime = Join-Path $RuntimeRoot "gateway"
$OneDriveRuntime = Join-Path $RuntimeRoot "onedrive"
$WebRuntime = Join-Path $RuntimeRoot "web"
$ConversationRuntime = Join-Path $RuntimeRoot "conversation"

$RuntimeDirectories = @(
    $ChromaRuntime,
    $IngestionRuntime,
    $RagRuntime,
    $GatewayRuntime,
    $OneDriveRuntime,
    $WebRuntime
    $ConversationRuntime
)

foreach ($Directory in $RuntimeDirectories) {
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null

    $RuntimeEnvFile = Join-Path $Directory ".env"
    if (Test-Path $RuntimeEnvFile) {
        Remove-Item $RuntimeEnvFile -Force
    }
}

# Load the application .env into this process. Child services inherit these
# variables, while Chroma cannot parse the root .env from its clean runtime dir.
if (-not (Test-Path $EnvFile)) {
    throw "Application environment file not found: $EnvFile"
}

Write-Host "Loading application environment variables..." -ForegroundColor Cyan

Get-Content $EnvFile | ForEach-Object {
    $Line = $_.Trim()

    if ([string]::IsNullOrWhiteSpace($Line) -or $Line.StartsWith("#")) {
        return
    }

    if ($Line.StartsWith("export ")) {
        $Line = $Line.Substring(7).Trim()
    }

    $Parts = $Line -split "=", 2
    if ($Parts.Count -ne 2) {
        return
    }

    $Name = $Parts[0].Trim()
    $Value = $Parts[1].Trim()

    if ([string]::IsNullOrWhiteSpace($Name)) {
        return
    }

    if (
        ($Value.StartsWith('"') -and $Value.EndsWith('"')) -or
        ($Value.StartsWith("'") -and $Value.EndsWith("'"))
    ) {
        $Value = $Value.Substring(1, $Value.Length - 2)
    }

    [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
}

# Stop existing processes on target ports.
Write-Host "Checking and stopping existing services on ports 8000, 8001, 8002, 8003, 8004, 3000..." -ForegroundColor Cyan
$Ports = @(8000, 8001, 8002, 8003, 8004, 8005, 3000)

foreach ($Port in $Ports) {
    $Connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue

    foreach ($Connection in $Connections) {
        $OwningPid = $Connection.OwningProcess
        if ($OwningPid -gt 0) {
            Write-Host "-> Stopping process $OwningPid on port $Port..." -ForegroundColor Yellow
            Stop-Process -Id $OwningPid -Force -ErrorAction SilentlyContinue
        }
    }
}

Start-Sleep -Seconds 1

# Create virtual environment if missing.
if (-not (Test-Path $PythonExe)) {
    Write-Host "Creating virtual environment 'venv'..." -ForegroundColor Cyan
    python -m venv (Join-Path $ProjectRoot "venv")
}

# Install dependencies only when required. Normal startup can use
# -SkipDependencyInstall to avoid reinstalling packages every time.
if (-not $SkipDependencyInstall) {
    Write-Host "Installing dependencies in virtual environment..." -ForegroundColor Cyan

    & $PythonExe -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }

    & $PythonExe -m pip install `
        -r (Join-Path $ProjectRoot "services\gateway\requirements.txt") `
        -r (Join-Path $ProjectRoot "services\ingestion\requirements.txt") `
        -r (Join-Path $ProjectRoot "services\rag_engine\requirements.txt") `
        -r (Join-Path $ProjectRoot "services\onedrive_connector\requirements.txt") `
        -r (Join-Path $ProjectRoot "services\conversation_service\requirements.txt")

    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install service dependencies."
    }

    & $PythonExe -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "Python dependency validation failed."
    }
}
else {
    Write-Host "Skipping dependency installation." -ForegroundColor DarkGray
}

# Verify Chroma version without importing chromadb Settings.
$ChromaVersion = & $PythonExe -c "from importlib.metadata import version; print(version('chromadb'))"
Write-Host "ChromaDB package version: $ChromaVersion" -ForegroundColor Cyan

# Ensure Ollama model exists.
Write-Host "Please ensure Ollama Desktop is running." -ForegroundColor Yellow
Write-Host "Pulling nomic-embed-text model if needed..." -ForegroundColor Cyan
ollama pull nomic-embed-text
if ($LASTEXITCODE -ne 0) {
    throw "Failed to pull Ollama model nomic-embed-text."
}

Write-Host "Starting all microservices in separate Command Prompt windows..." -ForegroundColor Green

$ChromaData = Join-Path $ProjectRoot "chroma_data"
$SharedData = Join-Path $ProjectRoot "shared_data"
$IngestionMain = Join-Path $ProjectRoot "services\ingestion\main.py"
$RagMain = Join-Path $ProjectRoot "services\rag_engine\main.py"
$ConversationMain = Join-Path $ProjectRoot "services\conversation_service\main.py"
$GatewayMain = Join-Path $ProjectRoot "services\gateway\main.py"
$OneDriveMain = Join-Path $ProjectRoot "services\onedrive_connector\main.py"
$WebDirectory = Join-Path $ProjectRoot "services\web_ui"

New-Item -ItemType Directory -Path $ChromaData -Force | Out-Null
New-Item -ItemType Directory -Path $SharedData -Force | Out-Null

# ChromaDB
Write-Host "-> Launching ChromaDB on http://127.0.0.1:8003" -ForegroundColor Green
$ChromaCommand = "title ChromaDB && `"$ChromaExe`" run --host 127.0.0.1 --port 8003 --path `"$ChromaData`""
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $ChromaCommand -WorkingDirectory $ChromaRuntime

# Ingestion
Write-Host "-> Launching Ingestion Service on http://127.0.0.1:8001" -ForegroundColor Green
$IngestionCommand = "title Ingestion Service && set `"PYTHONPATH=$ProjectRoot`" && `"$PythonExe`" `"$IngestionMain`""
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $IngestionCommand -WorkingDirectory $IngestionRuntime

# RAG
Write-Host "-> Launching RAG Query Engine on http://127.0.0.1:8002" -ForegroundColor Green
$RagCommand = "title RAG Engine && set `"PYTHONPATH=$ProjectRoot`" && `"$PythonExe`" `"$RagMain`""
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $RagCommand -WorkingDirectory $RagRuntime

# Conversation Service
Write-Host "-> Launching Conversation Service on http://127.0.0.1:8005" -ForegroundColor Green
$ConversationCommand = "title Conversation Service && set `"PYTHONPATH=$ProjectRoot`" && `"$PythonExe`" `"$ConversationMain`""
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $ConversationCommand -WorkingDirectory $ConversationRuntime

# Gateway
Write-Host "-> Launching API Gateway on http://127.0.0.1:8000" -ForegroundColor Green
$GatewayCommand = "title API Gateway && set `"PYTHONPATH=$ProjectRoot`" && `"$PythonExe`" `"$GatewayMain`""
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $GatewayCommand -WorkingDirectory $GatewayRuntime

# OneDrive connector
Write-Host "-> Launching OneDrive Connector on http://127.0.0.1:8004" -ForegroundColor Green
$OneDriveCommand = "title OneDrive Connector && set `"PYTHONPATH=$ProjectRoot`" && `"$PythonExe`" `"$OneDriveMain`""
Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $OneDriveCommand -WorkingDirectory $OneDriveRuntime

# Web UI: React + TypeScript + Vite
$WebDirectory = Join-Path $ProjectRoot "services\web_ui"

if (-not (Test-Path (Join-Path $WebDirectory "package.json"))) {
    throw "Không tìm thấy project Vite tại: $WebDirectory"
}

Write-Host "-> Launching React Web UI on port 3000" -ForegroundColor Green
$WebCommand = "title Web UI && npm.cmd run dev -- --host 0.0.0.0 --port 3000"
Start-Process `
    -FilePath "cmd.exe" `
    -ArgumentList "/k", $WebCommand `
    -WorkingDirectory $WebDirectory

Write-Host "`nService processes were launched." -ForegroundColor Green
Write-Host "Open http://localhost:3000 in your browser." -ForegroundColor Green
Write-Host "API Gateway: http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "To stop services, close their Command Prompt windows or rerun this script." -ForegroundColor Yellow

Write-Host `
    "Applying conversation database migrations..." `
    -ForegroundColor Cyan

& "$ProjectRoot\venv\Scripts\alembic.exe" `
    -c "$ProjectRoot\alembic.ini" `
    upgrade head

if ($LASTEXITCODE -ne 0) {
    throw (
        "Conversation database " + "migration failed.")
}
