"""API Gateway Service — Routes, authenticates, and rate-limits requests.

Central entry point for all client requests. Proxies to downstream services.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import sys

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.config import Settings, get_settings
from shared.logging_config import RequestLoggingMiddleware, setup_logging
from shared.models import (
    ChatRequest,
    ChatResponse,
    DocumentListResponse,
    Skill,
    SkillCreateRequest,
    SkillUpdateRequest,
    SkillListResponse,
    SkillType,
    HealthResponse,
    LogEntry,
    LogQueryRequest,
    LogQueryResponse,
    ServiceHealth,
)
from shared.security import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    get_current_user,
    detect_prompt_injection,
)
from shared import skills_repo

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

setup_logging("gateway", os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info("Gateway started. CORS origins: %s", settings.cors_origins_list)
    yield

app = FastAPI(
    title="RAG Chatbot API Gateway",
    version="0.1.0",
    description="Central API gateway for the RAG Chatbot application.",
    lifespan=lifespan,
)

# Middleware stack (order matters — outermost first)
settings = get_settings()

app.add_middleware(RequestLoggingMiddleware, service_name="gateway")
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, max_tokens=60, refill_rate=1.0)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["X-API-Key", "Content-Type"],
)


# ---------------------------------------------------------------------------
# HTTP Client for proxying
# ---------------------------------------------------------------------------

_http_client = None


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=30.0))
    return _http_client


# ---------------------------------------------------------------------------
# Chat Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/chat", dependencies=[Depends(get_current_user)])
async def chat_stream(request: ChatRequest):
    """Streaming chat endpoint — proxies to RAG engine via SSE."""
    if detect_prompt_injection(request.query):
        raise HTTPException(status_code=400, detail="Query rejected due to security policy (potential prompt injection).")
        
    settings = get_settings()
    client = await _get_client()

    try:
        # Forward to RAG engine streaming endpoint
        async def proxy_stream():
            async with client.stream(
                "POST",
                f"{settings.rag_engine_service_url}/rag/query",
                json=request.model_dump(),
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk

        return StreamingResponse(
            proxy_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="RAG engine service unavailable.")
    except Exception as e:
        logger.error("Chat stream error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")


@app.post("/api/v1/chat/sync", response_model=ChatResponse, dependencies=[Depends(get_current_user)])
async def chat_sync(request: ChatRequest):
    """Non-streaming chat endpoint."""
    if detect_prompt_injection(request.query):
        raise HTTPException(status_code=400, detail="Query rejected due to security policy (potential prompt injection).")
        
    settings = get_settings()
    client = await _get_client()

    try:
        response = await client.post(
            f"{settings.rag_engine_service_url}/rag/query/sync",
            json=request.model_dump(),
        )
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="RAG engine service unavailable.")
    except Exception as e:
        logger.error("Chat sync error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")


# ---------------------------------------------------------------------------
# Skills Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/v1/skills", response_model=SkillListResponse, dependencies=[Depends(validate_api_key)])
async def list_skills(type: str = None):
    """List all skills."""
    try:
        skill_type = SkillType(type) if type else None
        skills = skills_repo.list_skills(skill_type)
        return SkillListResponse(skills=skills, total=len(skills))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid skill type.")
    except Exception as e:
        logger.error("List skills error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")

@app.get("/api/v1/skills/{skill_id}", response_model=Skill, dependencies=[Depends(validate_api_key)])
async def get_skill(skill_id: str):
    """Get a skill by ID."""
    try:
        skill = skills_repo.get_skill(skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail="Skill not found.")
        return skill
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Get skill error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")

@app.post("/api/v1/skills", response_model=Skill, dependencies=[Depends(validate_api_key)])
async def create_skill(request: SkillCreateRequest):
    """Create a new custom skill."""
    try:
        return skills_repo.create_skill(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Create skill error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")

@app.put("/api/v1/skills/{skill_id}", response_model=Skill, dependencies=[Depends(validate_api_key)])
async def update_skill(skill_id: str, request: SkillUpdateRequest):
    """Update a custom skill."""
    try:
        return skills_repo.update_skill(skill_id, request)
    except skills_repo.SkillNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except skills_repo.SkillSystemError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Update skill error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")

@app.delete("/api/v1/skills/{skill_id}", dependencies=[Depends(validate_api_key)])
async def delete_skill(skill_id: str):
    """Delete a custom skill."""
    try:
        success = skills_repo.delete_skill(skill_id)
        if not success:
            raise HTTPException(status_code=404, detail="Skill not found.")
        return {"message": "Skill deleted successfully."}
    except skills_repo.SkillSystemError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Delete skill error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")


# ---------------------------------------------------------------------------
# Document Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/documents/upload", dependencies=[Depends(get_current_user)])
async def upload_document(file: UploadFile = File(...)):
    """Upload a document for ingestion."""
    settings = get_settings()
    client = await _get_client()

    try:
        # Read file content and forward to ingestion service
        content = await file.read()
        files = {"file": (file.filename, content, file.content_type or "application/octet-stream")}
        response = await client.post(
            f"{settings.ingestion_service_url}/ingest/upload",
            files=files,
        )
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Ingestion service unavailable.")
    except httpx.HTTPStatusError as e:
        # Forward the downstream error status and message
        try:
            detail = e.response.json().get("detail", "Upload failed.")
        except Exception:
            detail = "Upload failed."
        raise HTTPException(status_code=e.response.status_code, detail=detail)
    except Exception as e:
        logger.error("Document upload error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")


@app.get("/api/v1/documents", response_model=DocumentListResponse, dependencies=[Depends(get_current_user)])
async def list_documents():
    """List all indexed documents."""
    settings = get_settings()
    client = await _get_client()

    try:
        response = await client.get(f"{settings.ingestion_service_url}/ingest/documents")
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Ingestion service unavailable.")
    except Exception as e:
        logger.error("List documents error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")


@app.delete("/api/v1/documents/{document_id}", dependencies=[Depends(get_current_user)])
async def delete_document(document_id: str):
    """Delete a document and its embeddings."""
    settings = get_settings()
    client = await _get_client()

    try:
        response = await client.delete(
            f"{settings.ingestion_service_url}/ingest/documents/{document_id}"
        )
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Ingestion service unavailable.")
    except httpx.HTTPStatusError as e:
        try:
            detail = e.response.json().get("detail", "Delete failed.")
        except Exception:
            detail = "Delete failed."
        raise HTTPException(status_code=e.response.status_code, detail=detail)
    except Exception as e:
        logger.error("Delete document error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")


# ---------------------------------------------------------------------------
# OneDrive/SharePoint Endpoints (Phase 2)
# ---------------------------------------------------------------------------

@app.post("/api/v1/onedrive/sync", dependencies=[Depends(get_current_user)])
async def trigger_onedrive_sync():
    """Trigger a manual sync from OneDrive/SharePoint."""
    settings = get_settings()
    client = await _get_client()

    try:
        response = await client.post(f"{settings.onedrive_connector_url}/onedrive/sync")
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="OneDrive Connector service unavailable.")
    except httpx.HTTPStatusError as e:
        try:
            detail = e.response.json().get("detail", "Sync failed.")
        except Exception:
            detail = "Sync failed."
        raise HTTPException(status_code=e.response.status_code, detail=detail)
    except Exception as e:
        logger.error("Sync trigger error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")

@app.get("/api/v1/onedrive/status", dependencies=[Depends(get_current_user)])
async def onedrive_sync_status():
    """Get the status of the last sync operation."""
    settings = get_settings()
    client = await _get_client()

    try:
        response = await client.get(f"{settings.onedrive_connector_url}/onedrive/status")
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="OneDrive Connector service unavailable.")
    except Exception as e:
        logger.error("Sync status error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")

@app.get("/api/v1/onedrive/auth-url", dependencies=[Depends(get_current_user)])
async def onedrive_auth_url():
    """Get the Microsoft login URL."""
    settings = get_settings()
    client = await _get_client()

    try:
        response = await client.get(f"{settings.onedrive_connector_url}/onedrive/auth-url")
        response.raise_for_status()
        return response.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="OneDrive Connector service unavailable.")
    except Exception as e:
        logger.error("Auth URL error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")

# Note: No API key validation on the callback because Microsoft redirects the user's browser directly here!
@app.get("/api/v1/onedrive/callback")
async def onedrive_callback(request: Request):
    """Handle the OAuth callback from Microsoft."""
    settings = get_settings()
    client = await _get_client()

    try:
        # We must pass the exact query string (which contains the auth code) down to the connector
        query_string = request.url.query
        connector_url = f"{settings.onedrive_connector_url}/onedrive/callback"
        if query_string:
            connector_url = f"{connector_url}?{query_string}"
            
        # Don't follow redirects, just return the redirect back to the user's browser
        response = await client.get(connector_url, follow_redirects=False)
        
        from fastapi.responses import Response
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers)
        )
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="OneDrive Connector service unavailable.")
    except Exception as e:
        logger.error("Auth callback error: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred.")

# ---------------------------------------------------------------------------
# Logs Endpoint (proxies to Loki)
# ---------------------------------------------------------------------------

@app.post("/api/v1/logs", response_model=LogQueryResponse, dependencies=[Depends(get_current_user)])
async def query_logs(request: LogQueryRequest):
    """Query application logs via Loki."""
    settings = get_settings()
    client = await _get_client()

    try:
        # Build LogQL query
        label_filters = []
        if request.service:
            label_filters.append(f'container_name=~".*{request.service}.*"')

        label_selector = "{" + ",".join(label_filters) + "}" if label_filters else '{job="docker"}'

        line_filters = ""
        if request.level:
            line_filters += f' |= "{request.level}"'
        if request.search:
            line_filters += f' |= "{request.search}"'

        logql = f"{label_selector}{line_filters}"

        params = {"query": logql, "limit": str(request.limit)}
        if request.start_time:
            params["start"] = str(int(request.start_time.timestamp() * 1e9))
        if request.end_time:
            params["end"] = str(int(request.end_time.timestamp() * 1e9))

        response = await client.get(
            f"{settings.loki_url}/loki/api/v1/query_range",
            params=params,
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()

        # Parse Loki response into log entries
        logs = []
        results = data.get("data", {}).get("result", [])
        for stream in results:
            for value in stream.get("values", []):
                if len(value) >= 2:
                    from datetime import datetime, timezone
                    timestamp = datetime.fromtimestamp(
                        int(value[0]) / 1e9, tz=timezone.utc
                    )
                    import json as json_mod
                    try:
                        log_data = json_mod.loads(value[1])
                        logs.append(
                            LogEntry(
                                timestamp=timestamp,
                                service=log_data.get("service", "unknown"),
                                level=log_data.get("level", "INFO"),
                                message=log_data.get("message", value[1]),
                                request_id=log_data.get("request_id"),
                            )
                        )
                    except (json_mod.JSONDecodeError, KeyError):
                        logs.append(
                            LogEntry(
                                timestamp=timestamp,
                                service="unknown",
                                level="INFO",
                                message=value[1],
                            )
                        )

        return LogQueryResponse(logs=logs, total=len(logs))

    except httpx.ConnectError:
        logger.warning("Loki unavailable for log query.")
        return LogQueryResponse(logs=[], total=0)
    except Exception as e:
        logger.error("Log query error: %s", str(e))
        return LogQueryResponse(logs=[], total=0)


# ---------------------------------------------------------------------------
# Health Endpoint
# ---------------------------------------------------------------------------

@app.get("/api/v1/health", response_model=HealthResponse)
async def health_check():
    """Aggregated health check from all services."""
    settings = get_settings()
    client = await _get_client()
    services = []

    # Check each downstream service
    for name, url in [
        ("ingestion", f"{settings.ingestion_service_url}/ingest/health"),
        ("rag_engine", f"{settings.rag_engine_service_url}/rag/health"),
    ]:
        try:
            resp = await client.get(url, timeout=5.0)
            resp.raise_for_status()
            data = resp.json()
            services.append(ServiceHealth(**data))
        except Exception:
            services.append(
                ServiceHealth(service=name, status="unhealthy", details="unreachable")
            )

    # Gateway is healthy if it can respond
    services.insert(
        0, ServiceHealth(service="gateway", status="healthy", version="0.1.0")
    )

    overall = "healthy" if all(s.status == "healthy" for s in services) else "degraded"
    return HealthResponse(
        status=overall,
        services=services,
        model_name=settings.ollama_model,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
