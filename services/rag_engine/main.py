from __future__ import annotations
import json
import logging
import os
import sys
import time
from fastapi import Depends, FastAPI, HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from shared.config import Settings, get_settings
from shared.logging_config import RequestLoggingMiddleware, setup_logging
from shared.models import ChatResponse, ServiceHealth, SourceDocument
from shared.security import SecurityHeadersMiddleware
from retriever import Retriever
from prompt_builder import build_rag_prompt
from llm_client import OllamaLLMClient
from pdf_compare_tool import ComparisonChatRequest, prepare_comparison

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
_retriever = None
_llm_client = None


def _get_retriever():
    global _retriever
    if _retriever is None:
        s = get_settings()
        _retriever = Retriever(
            chroma_host=s.chroma_host,
            collection_name=s.chroma_collection,
            ollama_host=s.ollama_host,
            embed_model=s.ollama_embed_model,
        )
    return _retriever


def _get_llm_client():
    global _llm_client
    if _llm_client is None:
        s = get_settings()
        _llm_client = OllamaLLMClient(
            ollama_host=s.ollama_host, model=s.ollama_model, timeout=s.ollama_timeout
        )
    return _llm_client


def _source(r):
    m = r["metadata"]
    return {
        "document_id": m.get("document_id", ""),
        "filename": m.get("filename", "unknown"),
        "chunk_index": m.get("chunk_index", 0),
        "content": r["text"][:300],
        "score": r["score"],
        "page": m.get("page"),
    }


@app.post("/rag/query/sync", response_model=ChatResponse)
async def query_sync(
    request: ComparisonChatRequest, settings: Settings = Depends(get_settings)
):
    start = time.perf_counter()
    try:
        llm = _get_llm_client()
        comparison = await prepare_comparison(request, llm)
        if comparison is not None:
            messages, _ = comparison
            answer = (
                await llm.chat_once(messages, temperature=request.temperature)
            ).get("content", "")
            sources = []
        else:
            results = await _get_retriever().search(request.query, top_k=request.top_k)
            answer = await llm.generate(
                prompt=build_rag_prompt(query=request.query, context_chunks=results),
                temperature=request.temperature,
            )
            sources = [SourceDocument(**_source(r)) for r in results]
        return ChatResponse(
            answer=answer,
            sources=sources,
            query=request.query,
            model=settings.ollama_model,
            processing_time_ms=round((time.perf_counter() - start) * 1000, 1),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "RAG downstream HTTP error: method=%s url=%s status=%s body=%s",
            exc.request.method,
            exc.request.url,
            exc.response.status_code,
            exc.response.text[:1000],
        )
        raise HTTPException(status_code=500, detail="Unable to process query.") from exc


@app.post("/rag/query")
async def query_stream(request: ComparisonChatRequest):
    from sse_starlette.sse import EventSourceResponse

    async def events():
        try:
            llm = _get_llm_client()
            comparison = await prepare_comparison(request, llm)
            if comparison is not None:
                messages, data = comparison
                yield {"event": "sources", "data": "[]"}
                yield {
                    "event": "comparison",
                    "data": json.dumps(data, ensure_ascii=False),
                }
                async for token in llm.chat_stream_messages(
                    messages, temperature=request.temperature
                ):
                    yield {
                        "event": "token",
                        "data": json.dumps(token, ensure_ascii=False),
                    }
            else:
                results = await _get_retriever().search(
                    request.query, top_k=request.top_k
                )
                yield {
                    "event": "sources",
                    "data": json.dumps(
                        [_source(r) for r in results], ensure_ascii=False
                    ),
                }
                prompt = build_rag_prompt(query=request.query, context_chunks=results)
                async for token in llm.generate_stream(
                    prompt=prompt, temperature=request.temperature
                ):
                    yield {
                        "event": "token",
                        "data": json.dumps(token, ensure_ascii=False),
                    }
            yield {"event": "done", "data": "{}"}
        except Exception as exc:
            logger.error(
                        "RAG downstream HTTP error: method=%s url=%s status=%s body=%s",
                        exc.request.method,
                        exc.request.url,
                        exc.response.status_code,
                        exc.response.text[:1000],
                    )
            yield {"event": "error", "data": json.dumps("Unable to process query.")}

    return EventSourceResponse(events())


@app.get("/rag/health", response_model=ServiceHealth)
async def health_check():
    try:
        ok = await _get_llm_client().health_check()
    except Exception:
        ok = False
    return ServiceHealth(
        service="rag_engine",
        status="healthy" if ok else "degraded",
        version="0.1.0",
        details=f"Ollama: {'connected' if ok else 'unavailable'}",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
