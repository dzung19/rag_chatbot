# =============================================================================
# RAG Chatbot — Local Non-Docker Startup Script (Windows PowerShell)
# =============================================================================

$ErrorActionPreference = "Stop"

# Stop existing processes running on the target ports to avoid port conflict issues
Write-Host "Checking and stopping any existing services on ports 8000, 8001, 8002, 8003, 8004, 3000..." -ForegroundColor Cyan
$ports = @(8000, 8001, 8002, 8003, 8004, 3000)
foreach ($port in $ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
    if ($conns) {
        foreach ($conn in $conns) {
            $owningPid = $conn.OwningProcess
            if ($owningPid -gt 0) {
                Write-Host "-> Found active process ($owningPid) on port $port. Stopping..." -ForegroundColor Yellow
                Stop-Process -Id $owningPid -Force -ErrorAction SilentlyContinue
            }
        }
    }
}
Start-Sleep -Seconds 1

# 1. Setup Virtual Environment if not present
if (-not (Test-Path "venv")) {
    Write-Host "Creating virtual environment 'venv'..." -ForegroundColor Cyan
    python -m venv venv
}

# 2. Upgrade pip and install all service requirements
Write-Host "Installing dependencies in virtual environment..." -ForegroundColor Cyan
& .\venv\Scripts\python.exe -m pip install --upgrade pip
& .\venv\Scripts\pip.exe install -r services/gateway/requirements.txt -r services/ingestion/requirements.txt -r services/rag_engine/requirements.txt -r services/onedrive_connector/requirements.txt

# 3. Double-check Ollama model pre-reqs
Write-Host "Please ensure Ollama Desktop is running." -ForegroundColor Yellow
Write-Host "Pulling nomic-embed-text model (if not already downloaded)..." -ForegroundColor Cyan
ollama pull nomic-embed-text

# 4. Start services in separate windows
Write-Host "Starting all microservices in separate Command Prompt windows..." -ForegroundColor Green

# Start ChromaDB local server
Write-Host "-> Launching ChromaDB on http://127.0.0.1:8003" -ForegroundColor Green
Start-Process cmd -ArgumentList "/k", "title ChromaDB && .\venv\Scripts\chroma run --path ./chroma_data --port 8003"

# Start Ingestion Service
Write-Host "-> Launching Ingestion Service on http://127.0.0.1:8001" -ForegroundColor Green
Start-Process cmd -ArgumentList "/k", "title Ingestion Service && set PYTHONPATH=. && .\venv\Scripts\python services/ingestion/main.py"

# Start RAG Query Engine
Write-Host "-> Launching RAG Query Engine on http://127.0.0.1:8002" -ForegroundColor Green
Start-Process cmd -ArgumentList "/k", "title RAG Engine && set PYTHONPATH=. && .\venv\Scripts\python services/rag_engine/main.py"

# Start API Gateway
Write-Host "-> Launching API Gateway on http://127.0.0.1:8000" -ForegroundColor Green
Start-Process cmd -ArgumentList "/k", "title API Gateway && set PYTHONPATH=. && .\venv\Scripts\python services/gateway/main.py"

# Start OneDrive Connector
Write-Host "-> Launching OneDrive Connector on http://127.0.0.1:8004" -ForegroundColor Green
Start-Process cmd -ArgumentList "/k", "title OneDrive Connector && set PYTHONPATH=. && .\venv\Scripts\python services/onedrive_connector/main.py"

# Start simple local server for Web UI
Write-Host "-> Launching Web UI on http://localhost:3000" -ForegroundColor Green
Start-Process cmd -ArgumentList "/k", "title Web UI && .\venv\Scripts\python -m http.server 3000 --directory services/web_ui"

Write-Host "`nAll services successfully initiated!" -ForegroundColor Green
Write-Host "Open http://localhost:3000 in your browser to start chatting." -ForegroundColor Green
Write-Host "API Gateway is at http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "To shut down, close the individual service terminal windows." -ForegroundColor Yellow
