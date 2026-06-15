"""OneDrive/SharePoint Connector Service — Phase 2.

Crawls documents from OneDrive/SharePoint via Microsoft Graph API
and forwards them to the Ingestion Service.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, BackgroundTasks, HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.config import get_settings
from shared.logging_config import RequestLoggingMiddleware, setup_logging
from shared.models import ServiceHealth
from shared.security import SecurityHeadersMiddleware

from graph_client import GraphClient

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

setup_logging("onedrive_connector", os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# State
_sync_status = {
    "status": "idle",
    "last_sync": None,
    "files_synced": 0,
    "errors": 0,
    "is_running": False
}

# Supported file extensions for RAG ingestion
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md"}

app = FastAPI(
    title="OneDrive Connector Service",
    version="0.1.0",
    docs_url="/onedrive/docs",
    openapi_url="/onedrive/openapi.json",
)

app.add_middleware(RequestLoggingMiddleware, service_name="onedrive_connector")
app.add_middleware(SecurityHeadersMiddleware)

scheduler = AsyncIOScheduler()

# ---------------------------------------------------------------------------
# Core Logic
# ---------------------------------------------------------------------------

def _run_sync_blocking():
    """Synchronous implementation of the sync process using either local path or O365."""
    settings = get_settings()
    ingestion_url = f"{settings.ingestion_service_url}/ingest/upload"
    import requests

    if settings.sync_source_type == "local":
        paths_to_sync = settings.local_sync_paths_list
        if not paths_to_sync:
            raise Exception("No local sync paths configured.")
        
        for base_path in paths_to_sync:
            if not os.path.exists(base_path):
                logger.error("Local sync path is invalid or does not exist, skipping: %s", base_path)
                continue
                
            logger.info(f"Starting Local Sync from {base_path}...")
            
            for root, _, files in os.walk(base_path):
                for filename in files:
                    ext = Path(filename).suffix.lower()
                    
                    if ext not in SUPPORTED_EXTENSIONS:
                        continue
                        
                    file_path = os.path.join(root, filename)
                    
                    # Calculate relative path to use as the display name
                    try:
                        rel_path = os.path.relpath(file_path, base_path)
                        # Replace windows backslashes with forward slashes for cross-platform consistency in UI
                        display_name = rel_path.replace("\\", "/")
                    except ValueError:
                        display_name = filename
                        
                    try:
                        logger.info("Forwarding %s to ingestion service...", display_name)
                        with open(file_path, "rb") as f:
                            content = f.read()
                            
                        files_payload = {"file": (display_name, content, "application/octet-stream")}
                        resp = requests.post(ingestion_url, files=files_payload, timeout=60.0)
                        resp.raise_for_status()
                        
                        _sync_status["files_synced"] += 1
                    except Exception as e:
                        logger.error("Failed to sync file %s: %s", display_name, str(e))
                        _sync_status["errors"] += 1
                    
    else:
        # Check if credentials exist
        if not settings.azure_tenant_id or not settings.azure_client_id or not settings.azure_client_secret:
            raise Exception("Azure AD credentials not configured.")

        try:
            graph_client = GraphClient(
                tenant_id=settings.azure_tenant_id,
                client_id=settings.azure_client_id,
                client_secret=settings.azure_client_secret,
            )
        except ValueError as e:
            logger.error(str(e))
            raise

        if not graph_client.is_authenticated:
            raise Exception("O365_AUTH_REQUIRED")

        logger.info("Starting SharePoint sync (O365)...")
        
        # 1. Resolve Site
        site = graph_client.get_site(
            domain=settings.sharepoint_domain,
            site_path=settings.sharepoint_site_path
        )
        
        # 2. Resolve Drive
        drive = graph_client.get_drive(
            site=site,
            drive_name=settings.sharepoint_drive_name
        )
        
        # 3. Walk files
        for filename, content in graph_client.walk_drive_files(folder=drive.get_root_folder()):
            ext = Path(filename).suffix.lower()
            
            if ext not in SUPPORTED_EXTENSIONS:
                logger.debug("Skipping unsupported file: %s", filename)
                continue
                
            try:
                # 4. Push to ingestion service
                logger.info("Forwarding %s to ingestion service...", filename)
                files_payload = {"file": (filename, content, "application/octet-stream")}
                resp = requests.post(ingestion_url, files=files_payload, timeout=60.0)
                resp.raise_for_status()
                
                _sync_status["files_synced"] += 1
            except Exception as e:
                logger.error("Failed to sync file %s: %s", filename, str(e))
                _sync_status["errors"] += 1

        graph_client.close()

async def _perform_sync():
    """Background task to perform the actual sync operation."""
    if _sync_status["is_running"]:
        logger.warning("Sync already running. Skipping this trigger.")
        return

    _sync_status["is_running"] = True
    _sync_status["status"] = "syncing"
    _sync_status["files_synced"] = 0
    _sync_status["errors"] = 0

    try:
        await asyncio.to_thread(_run_sync_blocking)
        
        _sync_status["status"] = "success"
        _sync_status["last_sync"] = datetime.now(timezone.utc).isoformat()
        logger.info("SharePoint sync completed successfully. %d files synced.", _sync_status["files_synced"])
        
    except Exception as e:
        err_msg = str(e)
        logger.exception("SharePoint sync failed: %s", err_msg)
        _sync_status["status"] = "failed"
        if "O365_AUTH_REQUIRED" in err_msg:
            _sync_status["error_code"] = "O365_AUTH_REQUIRED"
    finally:
        _sync_status["is_running"] = False

# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schedule sync every Friday at 2:00 AM
    scheduler.add_job(
        _perform_sync,
        CronTrigger(day_of_week="fri", hour=2, minute=0),
        id="friday_sync",
        replace_existing=True
    )
    scheduler.start()
    logger.info("Started background scheduler for weekly Friday syncs.")
    
    yield  # The app runs while yielded
    
    scheduler.shutdown()
    logger.info("Stopped background scheduler.")

app.router.lifespan_context = lifespan

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/onedrive/sync")
async def trigger_sync(background_tasks: BackgroundTasks):
    """Trigger a manual sync from OneDrive/SharePoint."""
    if _sync_status["is_running"]:
        raise HTTPException(status_code=409, detail="A sync is already in progress.")
        
    # Check auth status synchronously before spawning the background task if using Azure
    settings = get_settings()
    if settings.sync_source_type == "azure":
        if settings.azure_tenant_id and settings.azure_client_id and settings.azure_client_secret:
            graph_client = GraphClient(
                tenant_id=settings.azure_tenant_id,
                client_id=settings.azure_client_id,
                client_secret=settings.azure_client_secret,
            )
            if not graph_client.is_authenticated:
                raise HTTPException(status_code=401, detail="O365_AUTH_REQUIRED")
            
    background_tasks.add_task(_perform_sync)
    return {"message": "Sync started in the background."}

@app.get("/onedrive/status")
async def sync_status():
    """Get the status of the last sync operation."""
    return _sync_status

from fastapi import Request
from fastapi.responses import RedirectResponse

# The redirect URI must match what's configured in Azure AND what the web app uses
# For local dev, this is the Gateway URL
REDIRECT_URI = "http://localhost:8000/api/v1/onedrive/callback"

@app.get("/onedrive/auth-url")
async def get_auth_url():
    """Get the Microsoft login URL."""
    settings = get_settings()
    graph_client = GraphClient(
        tenant_id=settings.azure_tenant_id,
        client_id=settings.azure_client_id,
        client_secret=settings.azure_client_secret,
    )
    url = graph_client.get_auth_url(redirect_uri=REDIRECT_URI)
    return {"url": url}

@app.get("/onedrive/callback")
async def auth_callback(request: Request):
    """Handle the OAuth callback from Microsoft."""
    settings = get_settings()
    graph_client = GraphClient(
        tenant_id=settings.azure_tenant_id,
        client_id=settings.azure_client_id,
        client_secret=settings.azure_client_secret,
    )
    
    # We reconstruct the full URL that was requested, but use HTTP/HTTPS appropriately
    # The current_url needs to match the Redirect URI format
    # request.url is a starlette URL object
    current_url = str(request.url)
    
    success = graph_client.process_auth_callback(current_url=current_url, redirect_uri=REDIRECT_URI)
    
    if success:
        # Redirect back to the main UI
        return RedirectResponse(url="http://localhost:3000/")
    else:
        raise HTTPException(status_code=400, detail="Failed to authenticate with O365.")

@app.get("/onedrive/health", response_model=ServiceHealth)
async def health_check():
    """Health check endpoint."""
    return ServiceHealth(
        service="onedrive_connector",
        status="healthy",
        version="0.1.0",
        details="Scheduler is active.",
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
