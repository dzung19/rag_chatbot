"""RAG Query Engine Service — Retrieves context and generates answers.

Pipeline: Query → Embed → Search ChromaDB → Build Prompt → LLM → Stream Response.
"""

from __future__ import annotations

import logging
import os
import sys
import time

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.config import Settings, get_settings
from shared.logging_config import RequestLoggingMiddleware, setup_logging
from shared.models import ChatRequest, ChatResponse, ServiceHealth, SourceDocument
from shared.security import SecurityHeadersMiddleware

from retriever import Retriever
from prompt_builder import build_rag_prompt
from llm_client import OllamaLLMClient

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

setup_logging("rag_engine", os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

app = FastAPI(
    title="RAG Query Engine Service",
    version="0.1.0",
    docs_url="/rag/docs",
    openapi_url="/rag/openapi.json",
)

app.add_middleware(RequestLoggingMiddleware, service_name="rag_engine")
app.add_middleware(SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# Service clients (initialized lazily)
# ---------------------------------------------------------------------------

_retriever = None
_llm_client = None


def _get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        settings = get_settings()
        _retriever = Retriever(
            chroma_host=settings.chroma_host,
            collection_name=settings.chroma_collection,
            ollama_host=settings.ollama_host,
            embed_model=settings.ollama_embed_model,
        )
    return _retriever


def _get_llm_client() -> OllamaLLMClient:
    global _llm_client
    if _llm_client is None:
        settings = get_settings()
        _llm_client = OllamaLLMClient(
            ollama_host=settings.ollama_host,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout,
        )
    return _llm_client


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/rag/query/sync", response_model=ChatResponse)
async def query_sync(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),
):
    """Non-streaming RAG query — returns complete response."""
    start_time = time.perf_counter()

    try:
        # 1. Retrieve relevant context
        retriever = _get_retriever()
        results = await retriever.search(request.query, top_k=request.top_k)

        if not results:
            logger.info("No relevant context found for query: %s", request.query[:100])

        # 2. Build prompt
        prompt = build_rag_prompt(
            query=request.query,
            context_chunks=results,
        )

        # 3. Generate answer via LLM
        llm = _get_llm_client()
        answer = await llm.generate(
            prompt=prompt,
            temperature=request.temperature,
        )

        # Post-process to clean LaTeX math arrows to simple Unicode arrows
        import re
        answer = re.sub(
            r'\$\\(?:right|left|up|down)arrow\$',
            lambda m: "→" if "right" in m.group(0).lower() else "←" if "left" in m.group(0).lower() else "↑" if "up" in m.group(0).lower() else "↓",
            answer,
            flags=re.IGNORECASE
        )
        answer = re.sub(
            r'\\(?:right|left|up|down)arrow',
            lambda m: "→" if "right" in m.group(0).lower() else "←" if "left" in m.group(0).lower() else "↑" if "up" in m.group(0).lower() else "↓",
            answer,
            flags=re.IGNORECASE
        )
        answer = re.sub(r'\$\\(?:Right|Left)arrow\$', lambda m: "⇒" if "Right" in m.group(0) else "⇐", answer)
        answer = re.sub(r'\\(?:Right|Left)arrow', lambda m: "⇒" if "Right" in m.group(0) else "⇐", answer)
        answer = re.sub(r'\$\\to\$', "→", answer)
        answer = re.sub(r'\\to\b', "→", answer)
        answer = answer.replace("-->", "→").replace("->", "→").replace("==>", "⇒").replace("=>", "⇒")

        # 4. Build source documents
        sources = [
            SourceDocument(
                document_id=r["metadata"].get("document_id", ""),
                filename=r["metadata"].get("filename", "unknown"),
                chunk_index=r["metadata"].get("chunk_index", 0),
                content=r["text"][:300],  # Truncate for response
                score=r["score"],
                page=r["metadata"].get("page"),
            )
            for r in results
        ]

        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "Query processed in %.1fms: '%s' → %d sources",
            duration_ms,
            request.query[:80],
            len(sources),
        )

        return ChatResponse(
            answer=answer,
            sources=sources,
            query=request.query,
            model=settings.ollama_model,
            processing_time_ms=round(duration_ms, 1),
        )

    except Exception as e:
        logger.error("RAG query failed: %s", str(e))
        raise HTTPException(status_code=500, detail="An error occurred processing your query.")


@app.post("/rag/query")
async def query_stream(request: ChatRequest):
    """Streaming RAG query — returns SSE stream."""
    from sse_starlette.sse import EventSourceResponse

    async def event_generator():
        try:
            # 1. Retrieve context
            retriever = _get_retriever()
            results = await retriever.search(request.query, top_k=request.top_k)

            # Send sources first
            import json
            sources = [
                {
                    "document_id": r["metadata"].get("document_id", ""),
                    "filename": r["metadata"].get("filename", "unknown"),
                    "chunk_index": r["metadata"].get("chunk_index", 0),
                    "content": r["text"][:300],
                    "score": r["score"],
                }
                for r in results
            ]
            yield {"event": "sources", "data": json.dumps(sources)}

            # 2. Build prompt
            prompt = build_rag_prompt(query=request.query, context_chunks=results)

            # 3. Stream LLM response
            llm = _get_llm_client()
            async for token in llm.generate_stream(
                prompt=prompt,
                temperature=request.temperature,
            ):
                yield {"event": "token", "data": json.dumps(token)}

            yield {"event": "done", "data": ""}

        except Exception as e:
            logger.error("Streaming query failed: %s", str(e))
            yield {"event": "error", "data": "An error occurred processing your query."}

    return EventSourceResponse(event_generator())


@app.get("/rag/health", response_model=ServiceHealth)
async def health_check():
    """Health check endpoint."""
    ollama_ok = False
    try:
        llm = _get_llm_client()
        ollama_ok = await llm.health_check()
    except Exception:
        pass

    return ServiceHealth(
        service="rag_engine",
        status="healthy" if ollama_ok else "degraded",
        version="0.1.0",
        details=f"Ollama: {'connected' if ollama_ok else 'unavailable'}",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
