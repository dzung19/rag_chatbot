"""RAG Query Engine Service — Retrieves context and generates answers.

Pipeline: Query → Preprocess → Parallel Hybrid Search (Vector + BM25) → RRF →
          Prompt Building (with Context Budgeting) → LLM (Gemma 4 CPU) →
          LaTeX Cleaning → Stream Response (SSE).
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import AsyncGenerator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.config import Settings, get_settings
from shared.logging_config import RequestLoggingMiddleware, setup_logging
from shared.models import ChatRequest, ChatResponse, ServiceHealth, SourceDocument
from shared.security import SecurityHeadersMiddleware

from retriever import Retriever
from prompt_builder import build_rag_messages, build_rag_prompt
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
            qdrant_url=settings.qdrant_url,
            qdrant_api_key=settings.qdrant_api_key,
            collection_name=settings.qdrant_collection,
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
            num_threads=settings.ollama_num_threads,
        )
    return _llm_client


# ---------------------------------------------------------------------------
# LaTeX Arrow Post-Processing Helpers
# ---------------------------------------------------------------------------

def _clean_latex_arrows(text: str) -> str:
    """Clean LaTeX math arrow blocks and ASCII arrows to standard Unicode arrows."""
    if not text:
        return ""
    # 1. LaTeX math blocks: $\rightarrow$, $\leftarrow$, etc.
    text = re.sub(
        r"\$\s*\\(?:right|left|up|down)arrow\s*\$",
        lambda m: (
            "→"
            if "right" in m.group(0).lower()
            else "←"
            if "left" in m.group(0).lower()
            else "↑"
            if "up" in m.group(0).lower()
            else "↓"
        ),
        text,
        flags=re.IGNORECASE,
    )
    # 2. Bare LaTeX arrow commands
    text = re.sub(
        r"\\(?:right|left|up|down)arrow\b",
        lambda m: (
            "→"
            if "right" in m.group(0).lower()
            else "←"
            if "left" in m.group(0).lower()
            else "↑"
            if "up" in m.group(0).lower()
            else "↓"
        ),
        text,
        flags=re.IGNORECASE,
    )
    # 3. Double arrows and to
    text = re.sub(
        r"\$\s*\\(?:Right|Left)arrow\s*\$",
        lambda m: "⇒" if "Right" in m.group(0) else "⇐",
        text,
    )
    text = re.sub(
        r"\\(?:Right|Left)arrow\b",
        lambda m: "⇒" if "Right" in m.group(0) else "⇐",
        text,
    )
    text = re.sub(r"\$\s*\\to\s*\$", "→", text)
    text = re.sub(r"\\to\b", "→", text)
    # 4. Clean residual math-wrapped arrows: $→$
    text = re.sub(r"\$\s*([→←↑↓⇒⇐])\s*\$", r"\1", text)
    # 5. ASCII arrows
    text = (
        text.replace("-->", "→")
        .replace("->", "→")
        .replace("==>", "⇒")
        .replace("=>", "⇒")
    )
    return text


async def _clean_stream_tokens(
    token_gen: AsyncGenerator[str, None],
) -> AsyncGenerator[str, None]:
    """Buffer stream tokens to detect and convert LaTeX arrows before SSE emit."""
    buffer = ""
    async for token in token_gen:
        buffer += token
        cleaned = _clean_latex_arrows(buffer)

        # Check for incomplete LaTeX patterns in the tail
        tail = cleaned[-25:]
        indices = [tail.find(ch) for ch in ["$", "\\", "-", "="] if tail.find(ch) != -1]

        if indices:
            first_trigger = min(indices)
            split_point = len(cleaned) - len(tail) + first_trigger
            to_yield = cleaned[:split_point]
            buffer = cleaned[split_point:]
            if to_yield:
                yield to_yield
        else:
            yield cleaned
            buffer = ""

    if buffer:
        yield _clean_latex_arrows(buffer)


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
        # 1. Retrieve relevant context via parallel hybrid search
        retriever = _get_retriever()
        results = await retriever.search(request.query, top_k=request.top_k)

        if not results:
            logger.info("No relevant context found for query: %s", request.query[:100])

        # 2. Build structured chat messages with token/character budgeting
        messages = build_rag_messages(
            query=request.query,
            context_chunks=results,
        )

        # 3. Generate answer via LLM
        llm = _get_llm_client()
        raw_answer = await llm.generate(
            messages=messages,
            temperature=request.temperature,
        )

        # Post-process to clean LaTeX math arrows to simple Unicode arrows
        answer = _clean_latex_arrows(raw_answer)

        # 4. Build source documents with enriched metadata
        sources = [
            SourceDocument(
                document_id=r["metadata"].get("document_id", ""),
                filename=r["metadata"].get("filename", "unknown"),
                chunk_index=r["metadata"].get("chunk_index", 0),
                content=r["text"][:300],  # Truncate for response payload
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
        logger.error("RAG query failed: %s", str(e), exc_info=True)
        raise HTTPException(
            status_code=500, detail="An error occurred processing your query."
        )


@app.post("/rag/query")
async def query_stream(request: ChatRequest):
    """Streaming RAG query — returns SSE stream with LaTeX arrow post-processing."""
    from sse_starlette.sse import EventSourceResponse

    async def event_generator():
        try:
            # 1. Retrieve context via parallel hybrid search
            retriever = _get_retriever()
            results = await retriever.search(request.query, top_k=request.top_k)

            # Send enriched sources first
            sources = [
                {
                    "document_id": r["metadata"].get("document_id", ""),
                    "filename": r["metadata"].get("filename", "unknown"),
                    "chunk_index": r["metadata"].get("chunk_index", 0),
                    "content": r["text"][:300],
                    "score": r["score"],
                    "page": r["metadata"].get("page"),
                    "heading": r["metadata"].get("heading"),
                }
                for r in results
            ]
            yield {"event": "sources", "data": json.dumps(sources)}

            # 2. Build structured messages with budgeting
            messages = build_rag_messages(
                query=request.query,
                context_chunks=results,
            )

            # 3. Stream LLM response through the arrow cleaner
            llm = _get_llm_client()
            token_gen = llm.generate_stream(
                messages=messages,
                temperature=request.temperature,
            )

            async for token in _clean_stream_tokens(token_gen):
                yield {"event": "token", "data": json.dumps(token)}

            yield {"event": "done", "data": ""}

        except Exception as e:
            logger.error("Streaming query failed: %s", str(e), exc_info=True)
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
