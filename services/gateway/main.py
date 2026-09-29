"""API Gateway Service — Routes, authenticates, and rate-limits requests.

Central entry point for all client requests. Proxies to downstream services.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import sys
from uuid import UUID

import httpx
from pydantic import Field
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
    HealthResponse,
    LogEntry,
    LogQueryRequest,
    LogQueryResponse,
    ServiceHealth,
)
from shared.security import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    validate_api_key,
    detect_prompt_injection,
)

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

class ComparisonChatRequest(ChatRequest):
    selected_document_ids: list[str] = Field(default_factory=list, max_length=2)


def _chat_payload(request: ComparisonChatRequest) -> dict:
    ids = request.selected_document_ids
    if len(ids) not in (0, 2):
        raise HTTPException(status_code=400, detail="Select exactly two PDFs.")
    payload = request.model_dump()
    if ids:
        try:
            ids = [str(UUID(item)) for item in ids]
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="Invalid PDF ID.") from exc
        if ids[0] == ids[1]:
            raise HTTPException(status_code=400, detail="Select two different PDFs.")
        payload["selected_document_ids"] = ids
    return payload


@app.post("/api/v1/chat", dependencies=[Depends(validate_api_key)])
async def chat_stream(request: ComparisonChatRequest):
    if detect_prompt_injection(request.query):
        raise HTTPException(status_code=400, detail="Query rejected due to security policy.")
    client = await _get_client()
    upstream = None
    try:
        req = client.build_request("POST", f"{get_settings().rag_engine_service_url}/rag/query",
                                   json=_chat_payload(request))
        upstream = await client.send(req, stream=True)
        upstream.raise_for_status()
    except httpx.HTTPStatusError as exc:
        await exc.response.aclose()
        status = exc.response.status_code
        raise HTTPException(status_code=status if status in (400, 403, 404, 422) else 502,
                            detail="RAG request rejected.") from exc
    except httpx.RequestError as exc:
        if upstream is not None:
            await upstream.aclose()
        raise HTTPException(status_code=503, detail="RAG service unavailable.") from exc

    async def relay():
        try:
            async for chunk in upstream.aiter_bytes():
                yield chunk
        except httpx.RequestError:
            logger.warning("RAG stream interrupted")
            yield b'event: error\ndata: "RAG stream interrupted."\n\n'
        finally:
            await upstream.aclose()
    return StreamingResponse(relay(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/v1/chat/sync", response_model=ChatResponse, dependencies=[Depends(validate_api_key)])
async def chat_sync(request: ComparisonChatRequest):
    if detect_prompt_injection(request.query):
        raise HTTPException(status_code=400, detail="Query rejected due to security policy.")
    client = await _get_client()
    try:
        response = await client.post(f"{get_settings().rag_engine_service_url}/rag/query/sync",
                                     json=_chat_payload(request))
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise HTTPException(status_code=status if status in (400, 403, 404, 422) else 502,
                            detail="RAG request rejected.") from exc
    except httpx.RequestError as exc:
        raise HTTPException(status_code=503, detail="RAG service unavailable.") from exc


# ---------------------------------------------------------------------------
# Document Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/documents/upload", dependencies=[Depends(validate_api_key)])
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


@app.get("/api/v1/documents", response_model=DocumentListResponse, dependencies=[Depends(validate_api_key)])
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


@app.delete("/api/v1/documents/{document_id}", dependencies=[Depends(validate_api_key)])
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

@app.post("/api/v1/onedrive/sync", dependencies=[Depends(validate_api_key)])
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

@app.get("/api/v1/onedrive/status", dependencies=[Depends(validate_api_key)])
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

@app.get("/api/v1/onedrive/auth-url", dependencies=[Depends(validate_api_key)])
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

@app.post("/api/v1/logs", response_model=LogQueryResponse, dependencies=[Depends(validate_api_key)])
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
