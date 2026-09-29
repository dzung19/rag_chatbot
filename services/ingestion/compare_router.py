from __future__ import annotations
import uuid
from collections import Counter
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from compare_pdfs import extract, compare
router = APIRouter()
def install(documents, upload_dir):
    @router.get("/ingest/compare")
    async def compare_uploaded_pdfs(left_document_id: str, right_document_id: str,
                                    key_column: int = Query(0, ge=0), value_column: int = Query(1, ge=0)):
        if key_column == value_column or left_document_id == right_document_id:
            raise HTTPException(status_code=400, detail="Choose distinct PDFs and columns.")
        paths = []
        for document_id in (left_document_id, right_document_id):
            try: uuid.UUID(document_id)
            except ValueError as exc: raise HTTPException(status_code=400, detail="Invalid PDF ID.") from exc
            info = documents.get(document_id)
            if info is None: raise HTTPException(status_code=404, detail="Document not found.")
            if getattr(info.file_type, "value", info.file_type) != "pdf":
                raise HTTPException(status_code=400, detail="Both files must be PDFs.")
            path = Path(upload_dir) / f"{document_id}.pdf"
            if not path.is_file(): raise HTTPException(status_code=404, detail="PDF not found on disk.")
            paths.append(path)
        try:
            (left, lw), (right, rw) = await run_in_threadpool(lambda: (
                extract(paths[0], key_column, value_column), extract(paths[1], key_column, value_column)))
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Unable to extract PDF values.") from exc
        rows = compare(left, right); warnings = lw + rw
        if not left or not right: warnings.append("At least one PDF has no extracted values; comparison incomplete.")
        return {"left_document_id": left_document_id, "right_document_id": right_document_id,
                "summary": dict(Counter(r["status"] for r in rows)), "warnings": warnings, "results": rows}
    return router
