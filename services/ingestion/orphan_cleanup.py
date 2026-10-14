"""Daily cleanup of orphaned ingestion data across Qdrant, SQLite and uploads."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from qdrant_client.models import (
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
)

from shared.sqlite_db import delete_document_from_sqlite, get_sqlite_connection

logger = logging.getLogger(__name__)


@dataclass
class CleanupReport:
    dry_run: bool
    grace_hours: int
    qdrant_without_sqlite: list[str] = field(default_factory=list)
    sqlite_without_qdrant: list[str] = field(default_factory=list)
    incomplete_in_qdrant: list[str] = field(default_factory=list)
    chunks_without_document: list[str] = field(default_factory=list)
    files_without_document: list[str] = field(default_factory=list)
    skipped_processing: list[str] = field(default_factory=list)
    deleted_ids: list[str] = field(default_factory=list)
    deleted_files: int = 0
    failed_ids: list[str] = field(default_factory=list)
    aborted_reason: str | None = None


def _parse_ts(value: Any) -> datetime | None:
    """Parse timestamp từ Qdrant/SQLite thành datetime UTC."""
    if not value:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            try:
                dt = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _scan_qdrant(client, collection: str) -> dict[str, dict[str, Any]]:
    """Đếm points và lấy metadata theo document_id."""
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "total_chunks": None, "ingested_at": None}
    )
    offset = None

    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=1000,
            with_payload=["document_id", "total_chunks", "ingested_at"],
            with_vectors=False,
            offset=offset,
        )

        for point in points:
            payload = point.payload or {}
            doc_id = payload.get("document_id")
            if not doc_id:
                continue

            item = stats[doc_id]
            item["count"] += 1

            if payload.get("total_chunks") is not None:
                item["total_chunks"] = int(payload["total_chunks"])

            ts = _parse_ts(payload.get("ingested_at"))
            if ts and (item["ingested_at"] is None or ts > item["ingested_at"]):
                item["ingested_at"] = ts

        if offset is None:
            break

    return dict(stats)


def _scan_sqlite() -> tuple[dict[str, datetime | None], set[str]]:
    conn = get_sqlite_connection()
    try:
        documents = {
            _parse_ts(row[1])
            for row in conn.execute(
                "SELECT document_id, created_at FROM full_documents"
            )
        }
        chunk_doc_ids = {
            row[0]
            for row in conn.execute(
                "SELECT DISTINCT document_id FROM document_chunks"
            )
        }
    finally:
        conn.close()

    return documents, chunk_doc_ids


def _delete_qdrant_document(client, collection: str, doc_id: str) -> None:
    client.delete(
        collection_name=collection,
        points_selector=FilterSelector(
            filter=Filter(
                must=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=doc_id),
                    )
                ]
            )
        ),
        wait=True,
    )


def run_orphan_cleanup(
    *,
    qdrant_client,
    collection: str,
    upload_dir: Path,
    is_processing: Callable[[str], bool],
    dry_run: bool = True,
    grace_hours: int = 24,
    max_delete: int = 50,
) -> CleanupReport:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=grace_hours)
    report = CleanupReport(dry_run=dry_run, grace_hours=grace_hours)

    def is_old(ts: datetime | None) -> bool:
        return ts is not None and ts < cutoff

    qdrant_stats = _scan_qdrant(qdrant_client, collection)
    sqlite_docs, chunk_doc_ids = _scan_sqlite()

    to_delete: set[str] = set()

    # 1. Qdrant có points nhưng SQLite không có tài liệu.
    for doc_id, item in qdrant_stats.items():
        if doc_id in sqlite_docs:
            continue
        if is_processing(doc_id):
            report.skipped_processing.append(doc_id)
            continue
        if is_old(item["ingested_at"]):
            report.qdrant_without_sqlite.append(doc_id)
            to_delete.add(doc_id)

    # 2 + 3. SQLite có tài liệu nhưng Qdrant trống hoặc thiếu chunk.
    for doc_id, created_at in sqlite_docs.items():
        if is_processing(doc_id):
            if is_old(created_at):
                # Đang processing quá lâu: chỉ báo cáo, không xóa.
                report.skipped_processing.append(doc_id)
            continue

        if not is_old(created_at):
            continue

        item = qdrant_stats.get(doc_id)
        if item is None:
            report.sqlite_without_qdrant.append(doc_id)
            to_delete.add(doc_id)
        elif item["total_chunks"] and item["count"] < item["total_chunks"]:
            report.incomplete_in_qdrant.append(doc_id)
            to_delete.add(doc_id)

    # 4. FTS chunks không có full_documents.
    for doc_id in chunk_doc_ids - sqlite_docs.keys():
        if not is_processing(doc_id):
            report.chunks_without_document.append(doc_id)
            to_delete.add(doc_id)

    # 5. File upload không thuộc tài liệu hợp lệ nào.
    valid_ids = set(sqlite_docs) - to_delete
    if upload_dir.exists():
        for path in upload_dir.iterdir():
            if not path.is_file():
                continue
            doc_id = path.stem
            if doc_id in valid_ids or doc_id in to_delete or is_processing(doc_id):
                continue
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            if modified < cutoff:
                report.files_without_document.append(path.name)

    # Chốt an toàn: quá nhiều mục thì dừng.
    total = len(to_delete) + len(report.files_without_document)
    if total > max_delete:
        report.aborted_reason = (
            f"{total} items exceed max_delete={max_delete}. Nothing deleted."
        )
        logger.warning("Orphan cleanup aborted: %s", report.aborted_reason)
        return report

    if dry_run:
        logger.info(
            "Orphan cleanup dry-run: %d documents, %d files would be deleted",
            len(to_delete),
            len(report.files_without_document),
        )
        return report

    # Thực hiện xóa.
    for doc_id in sorted(to_delete):
        if is_processing(doc_id):
            continue  # Kiểm tra lại ngay trước khi xóa.
        try:
            _delete_qdrant_document(qdrant_client, collection, doc_id)
            delete_document_from_sqlite(doc_id)
            for file_path in upload_dir.glob(f"{doc_id}.*"):
                file_path.unlink(missing_ok=True)
                report.deleted_files += 1
            report.deleted_ids.append(doc_id)
        except Exception:
            logger.exception("Orphan cleanup failed for document %s", doc_id)
            report.failed_ids.append(doc_id)

    for name in report.files_without_document:
        try:
            (upload_dir / name).unlink(missing_ok=True)
            report.deleted_files += 1
        except Exception:
            logger.exception("Failed to delete orphan file %s", name)

    logger.info(
        "Orphan cleanup done: deleted %d documents, %d files, %d failures",
        len(report.deleted_ids),
        report.deleted_files,
        len(report.failed_ids),
    )
    return report