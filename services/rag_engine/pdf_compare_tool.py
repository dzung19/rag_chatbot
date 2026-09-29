from __future__ import annotations
import json
import os
from uuid import UUID
import httpx
from fastapi import HTTPException
from pydantic import Field
from shared.models import ChatRequest
import logging


class ComparisonChatRequest(ChatRequest):
    selected_document_ids: list[str] = Field(default_factory=list, max_length=2)

logger = logging.getLogger(__name__)
COMPARE_TOOL = {
    "type": "function",
    "function": {
        "name": "compare_selected_pdfs",
        "description": "Compare values in the two selected PDFs when user explicitly asks to compare them.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
}


async def prepare_comparison(request: ComparisonChatRequest, llm):
    ids = request.selected_document_ids
    if not ids:
        return None
    if len(ids) != 2:
        raise HTTPException(status_code=400, detail="Select two PDFs.")
    try:
        left, right = [str(UUID(item)) for item in ids]
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid PDF ID.") from exc
    if left == right:
        raise HTTPException(status_code=400, detail="Select different PDFs.")
    messages = [
        {
            "role": "system",
            "content": "Call compare_selected_pdfs ONLY when the user asks to compare the selected PDFs; for other questions do not call tools. Never invent values.",
        },
        {"role": "user", "content": request.query},
    ]
    decision = await llm.chat_once(messages, tools=[COMPARE_TOOL], temperature=0)
    calls = decision.get("tool_calls") or []
    if not calls:
        return None
    if (
        len(calls) != 1
        or calls[0].get("function", {}).get("name") != "compare_selected_pdfs"
    ):
        raise HTTPException(status_code=422, detail="Unsupported tool call.")
    if (calls[0]["function"].get("arguments") or {}) not in ({}, "{}"):
        raise HTTPException(status_code=422, detail="Unexpected tool arguments.")
    base = os.environ.get("INGESTION_URL", "http://localhost:8001").rstrip("/")
    try:
        logger.info(
            "Calling comparison service: base=%s left=%s right=%s",
            base,
            left,
            right,
        )
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.get(
                f"{base}/ingest/compare",
                params={"left_document_id": left, "right_document_id": right},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        raise HTTPException(
            status_code=code if code in (400, 403, 404, 422) else 502,
            detail="PDF comparison failed.",
        ) from exc
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail="Comparison service unavailable."
        ) from exc
    rows = data.get("results", [])
    if not isinstance(rows, list):
        raise HTTPException(status_code=502, detail="Invalid comparison result.")
    compact = {
        "summary": data.get("summary", {}),
        "warnings": data.get("warnings", []),
        "results": rows[:30],
        "omitted_count": max(0, len(rows) - 30),
    }
    messages.append(
        {
            "role": "assistant",
            "content": decision.get("content", ""),
            "tool_calls": calls,
        }
    )
    messages.append(
        {
            "role": "tool",
            "tool_name": "compare_selected_pdfs",
            "content": json.dumps(compact, ensure_ascii=False),
        }
    )
    messages.append(
        {
            "role": "system",
            "content": "Answer only from tool data; flag ambiguous_duplicate and needs_review. If omitted_count > 0, say full results are in UI.",
        }
    )
    return messages, data
