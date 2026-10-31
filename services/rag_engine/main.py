"""RAG Query Engine Service — Retrieves context and generates answers.

Pipeline: Query → Preprocess → Parallel Hybrid Search (Vector + BM25) → RRF →
          Prompt Building (with Context Budgeting) → LLM (Gemma 4 CPU) →
          LaTeX Cleaning → Stream Response (SSE).
"""

from __future__ import annotations

import asyncio
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
from shared import skills_repo
from contextlib import aclosing

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
            max_concurrency=settings.llm_max_concurrency,
            queue_timeout=settings.llm_queue_timeout,
            num_ctx=settings.ollama_num_ctx,
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
        # 0. Resolve skills
        main_skill = None
        if request.skill_id:
            main_skill = skills_repo.get_skill(request.skill_id)
            
        modifier_skills = []
        for mod_id in request.modifier_skill_ids:
            mod = skills_repo.get_skill(mod_id)
            if mod:
                modifier_skills.append(mod)
                
        # Apply overrides
        top_k = request.top_k
        temperature = request.temperature
        if main_skill:
            if main_skill.top_k_override is not None:
                top_k = main_skill.top_k_override
            if main_skill.temperature_override is not None:
                temperature = main_skill.temperature_override

        # 1. Retrieve relevant context (bypassing vector search if business-compare)
        retriever = _get_retriever()
        if request.skill_id == "business-compare":
            if not request.selected_document_ids or len(request.selected_document_ids) != 2:
                logger.warning(
                    "Skill 'business-compare' requires exactly 2 documents, but got %d: %s",
                    len(request.selected_document_ids) if request.selected_document_ids else 0,
                    request.selected_document_ids,
                )
                return ChatResponse(
                    answer="Chế độ So sánh Nghiệp vụ yêu cầu chính xác 2 tài liệu để đối chiếu. Vui lòng đính kèm đủ 2 file.",
                    sources=[],
                    query=request.query,
                    model=settings.ollama_model,
                    processing_time_ms=round((time.perf_counter() - start_time) * 1000, 1),
                )

            logger.info(
                "Skill 'business-compare' active with 2 documents: %s. Fetching full documents (bypassing RAG).",
                request.selected_document_ids,
            )
            results = retriever.get_full_documents(request.selected_document_ids)
            if len(results) < 2:
                for _ in range(3):
                    await asyncio.sleep(0.5)
                    results = retriever.get_full_documents(request.selected_document_ids)
                    if len(results) == 2:
                        break

            if len(results) < 2:
                logger.warning(
                    "Could not load full documents for IDs %s (found %d/2).",
                    request.selected_document_ids,
                    len(results),
                )
                return ChatResponse(
                    answer=f"Không thể tải đủ nội dung 2 tài liệu để đối chiếu (tìm thấy {len(results)}/2 tài liệu). Vui lòng kiểm tra lại tài liệu và thử lại.",
                    sources=[],
                    query=request.query,
                    model=settings.ollama_model,
                    processing_time_ms=round((time.perf_counter() - start_time) * 1000, 1),
                )

            # Auto-identify which is Checklist and which is BL, reordering if necessary
            if len(results) == 2:
                from compare_engine import resolve_compare_roles
                doc_checklist, doc_bl, role_meta = resolve_compare_roles(results[0], results[1])
                results = [doc_checklist, doc_bl]
                logger.info(
                    "COMPARE roles identified: checklist='%s', bl='%s', swapped=%s",
                    role_meta.get("checklist_filename"),
                    role_meta.get("bl_filename"),
                    role_meta.get("swapped"),
                )
        else:
            where_filter = (
                {"document_ids": request.selected_document_ids}
                if request.selected_document_ids
                else None
            )
            results = await retriever.search(
                request.query,
                top_k=top_k,
                where_filter=where_filter,
            )

        if not results:
            logger.info("No relevant context found for query: %s", request.query[:100])

        # 2. Build structured chat messages with token/character budgeting
        messages = build_rag_messages(
            query=request.query,
            context_chunks=results,
            main_skill=main_skill,
            modifier_skills=modifier_skills,
        )

        # 3. Generate answer via LLM
        llm = _get_llm_client()
        raw_answer = await llm.generate(
            messages=messages,
            temperature=temperature,
            num_ctx=32768 if request.skill_id == "business-compare" else None,
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

    except TimeoutError as te:
        logger.error("Sync query timed out in LLM queue: %s", str(te))
        raise HTTPException(
            status_code=503,
            detail="Hệ thống đang quá tải (hàng đợi xử lý LLM đã hết thời gian chờ). Vui lòng thử lại sau.",
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
            # 0. Resolve skills
            main_skill = None
            if request.skill_id:
                main_skill = skills_repo.get_skill(request.skill_id)

            modifier_skills = []
            for mod_id in request.modifier_skill_ids:
                mod = skills_repo.get_skill(mod_id)
                if mod:
                    modifier_skills.append(mod)

            # Apply overrides
            top_k = request.top_k
            temperature = request.temperature
            if main_skill:
                if main_skill.top_k_override is not None:
                    top_k = main_skill.top_k_override
                if main_skill.temperature_override is not None:
                    temperature = main_skill.temperature_override

            # 1. Retrieve context (bypassing vector search if business-compare)
            retriever = _get_retriever()
            if request.skill_id == "business-compare":
                if not request.selected_document_ids or len(request.selected_document_ids) != 2:
                    logger.warning(
                        "Skill 'business-compare' requires exactly 2 documents, but got %d: %s",
                        len(request.selected_document_ids) if request.selected_document_ids else 0,
                        request.selected_document_ids,
                    )
                    warning_msg = "Chế độ So sánh Nghiệp vụ yêu cầu chính xác 2 tài liệu để đối chiếu. Vui lòng đính kèm đủ 2 file."
                    yield {"event": "sources", "data": json.dumps([])}
                    yield {"event": "token", "data": warning_msg}
                    yield {"event": "done", "data": json.dumps({"query": request.query, "total_chunks": 0})}
                    return

                logger.info(
                    "Skill 'business-compare' active with 2 documents: %s. Fetching full documents (bypassing RAG).",
                    request.selected_document_ids,
                )
                results = retriever.get_full_documents(request.selected_document_ids)
                if len(results) < 2:
                    for _ in range(3):
                        await asyncio.sleep(0.5)
                        results = retriever.get_full_documents(request.selected_document_ids)
                        if len(results) == 2:
                            break

                if len(results) < 2:
                    logger.warning(
                        "Could not load full documents for IDs %s (found %d/2).",
                        request.selected_document_ids,
                        len(results),
                    )
                    error_msg = f"Không thể tải đủ nội dung 2 tài liệu để đối chiếu (tìm thấy {len(results)}/2 tài liệu). Vui lòng kiểm tra lại tài liệu và thử lại."
                    yield {"event": "sources", "data": json.dumps([])}
                    yield {"event": "token", "data": error_msg}
                    yield {"event": "done", "data": json.dumps({"query": request.query, "total_chunks": 0})}
                    return

                # Auto-identify which is Checklist and which is BL, reordering if necessary
                if len(results) == 2:
                    from compare_engine import resolve_compare_roles
                    doc_checklist, doc_bl, role_meta = resolve_compare_roles(results[0], results[1])
                    results = [doc_checklist, doc_bl]
                    logger.info(
                        "COMPARE roles identified: checklist='%s', bl='%s', swapped=%s",
                        role_meta.get("checklist_filename"),
                        role_meta.get("bl_filename"),
                        role_meta.get("swapped"),
                    )
            else:
                where_filter = (
                    {"document_ids": request.selected_document_ids}
                    if request.selected_document_ids
                    else None
                )
                results = await retriever.search(
                    request.query,
                    top_k=top_k,
                    where_filter=where_filter,
                )

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

            logger.info(
                "COMPARE step=load_documents_done docs=%d chars=%s",
                len(results),
                [len(item.get("text") or "") for item in results],
            )
            # 2. Build structured messages with budgeting
            messages = build_rag_messages(
                query=request.query,
                context_chunks=results,
                main_skill=main_skill,
                modifier_skills=modifier_skills,
            )
            
            """ 
            Liability）andArticle17                                                                                                                   +
            M345JE,ENGLANDTEL+44(0)7795408300                                                                                                         +
            EMAIL:AGNIESZKA.CALKA-BONCZAK@BROTHER.CO.UK | documentoftiteanddelivery shallbemadetothenamedconsigneeonproductionof suchreasonableproofof+
            EORI:DE760551960552887 | identityasmayberequiredbyteCarrrInwitesswhereoftheundersigedunbealfftheCarierhas                                 +
            signed thenumberofBilsstatedhereunder,allof thistenoranddate.Where issuedasaBillofLadingdelivery                                          +
            VAT+864450220 | maybemadeagainstonlyone originalBilinwhichcase,theothers shallstandvoid.（Termscontinuedon                                +
            backhereof andenlargedcnpynf hack clausesis availablefrom the Carrier uponrequestoronCarrier's                                            +
            NotifyParty | websiteathttps:/www.yusen-logistics.com/services/end-to-end-transportation/ocean-freight-forwarding/ocean-                  +
            freight-forwarding-terms-and-conditions).                                                                                                 +
            BROTHER INTERNATIONAL EUROPE LTD.                                                                                                         +
            BROTHER HOUSE，1TAME STREET，GUIDE BRIDGE，                                                                                               +
            AUDENSHAW,MANCHESTER, | Export/lmportReference(fortheMerchant'sand/orCarrier'sreference only)                                             +
            M34 5JE,ENGLAND TEL+44(0)7795408300                                                                                                       +
            EMAIL:AGNIESZKA.CALKA-BONCZAK@BROTHER.CO.UK                                                                                               +
            ForwardingAgentReference(forfreightforwarder'sreferenceonly)                                                                              +
            VAT+864450220                                                                                                                             +
            --More-- 
            """

            # 3. Stream LLM response through the arrow cleaner
            llm = _get_llm_client()
            output_chars = 0
            output_events = 0

            async with aclosing(
                llm.generate_stream(
                    messages=messages,
                    temperature=temperature,
                    num_predict=(
                        4096
                        if request.skill_id == "business-compare"
                        else 1024
                    ),
                    num_ctx=(
                        32768
                        if request.skill_id == "business-compare"
                        else None
                    ),
                )
            ) as token_gen:
                async with aclosing(
                    _clean_stream_tokens(token_gen)
                ) as cleaned:
                    async for token in cleaned:
                        if not token:
                            continue

                        if output_events == 0:
                            logger.info(
                                "RAG first token event emitted."
                            )

                        output_events += 1
                        output_chars += len(token)

                        yield {
                            "event": "token",
                            "data": json.dumps(
                                token,
                                ensure_ascii=False,
                            ),
                        }

            logger.info(
                "RAG stream finished: events=%d chars=%d",
                output_events,
                output_chars,
            )

            if output_chars == 0:
                raise RuntimeError(
                    "No answer content remained after stream cleaning."
                )

            yield {"event": "done", "data": ""}

        except TimeoutError as te:
            logger.error("Streaming query timed out in LLM queue: %s", str(te))
            yield {
                "event": "error",
                "data": "Hệ thống đang quá tải (hàng đợi xử lý LLM đã hết thời gian chờ). Vui lòng thử lại sau giây lát.",
            }

        except Exception as e:
            logger.error("Streaming query failed: %s", str(e), exc_info=True)
            yield {"event": "error", "data": f"Lỗi xử lý phản hồi: {str(e)}"}

    return EventSourceResponse(event_generator())


@app.get("/rag/health", response_model=ServiceHealth)
async def health_check():
    """Health check endpoint."""
    ollama_ok = False
    concurrency_info = ""
    try:
        llm = _get_llm_client()
        ollama_ok = await llm.health_check()
        concurrency_info = f" | LLM Concurrency: {llm.active_requests}/{llm.max_concurrency} (queued: {llm.waiting_requests})"
    except Exception:
        pass

    return ServiceHealth(
        service="rag_engine",
        status="healthy" if ollama_ok else "degraded",
        version="0.1.0",
        details=f"Ollama: {'connected' if ollama_ok else 'unavailable'}{concurrency_info}",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
