"""One-off re-ingestion script.

Rebuilds every stored document with the current parser + chunker.

Chunk IDs and metadata schema changed (real page numbers, plus 'section',
'parent_index', 'parent_text'), so old vectors must be wiped rather than
upserted over: the new chunking produces a DIFFERENT number of chunks, and
an upsert would leave orphaned chunk_N+1..M behind forever.

Run INSIDE the ingestion container, from the service directory:

    docker compose exec -w /app/services/ingestion ingestion \\
        python reingest.py --dry-run
    docker compose exec -w /app/services/ingestion ingestion \\
        python reingest.py

Options:
    --dry-run       Show what would happen, change nothing.
    --only <id>     Re-ingest a single document_id.
    --keep-vectors  Skip the wipe (only safe if IDs are guaranteed stable).
    --skip-preflight  Wipe even if the parser check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from shared.sqlite_db import get_sqlite_connection  # noqa: E402

import main as ingestion  # noqa: E402

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-7s %(message)s",
)
logger = logging.getLogger("reingest")


# ---------------------------------------------------------------------------
# Step 0 - preflight: make sure the new code actually works before wiping
# ---------------------------------------------------------------------------


def preflight(targets: list[tuple[str, Path, str]]) -> bool:
    """Parse + chunk one document without touching the index.

    Wiping a working index and THEN discovering the parser is broken is the
    worst possible outcome, so we prove the pipeline dry-runs first.
    """
    from chunker import chunk_pages
    from document_parser import parse_document

    doc_id, path, original = targets[0]
    ext = path.suffix.lower().lstrip(".")
    logger.info("Preflight on %s ...", original)

    try:
        pages = list(parse_document(str(path), ext))
    except Exception as e:
        logger.error("Preflight FAILED - parser raised: %s", e)
        return False

    if not pages:
        logger.error("Preflight FAILED - parser produced no text.")
        return False

    try:
        chunks = chunk_pages(pages, source=original)
    except Exception as e:
        logger.error("Preflight FAILED - chunker raised: %s", e)
        return False

    if not chunks:
        logger.error("Preflight FAILED - chunker produced no chunks.")
        return False

    logger.info(
        "Preflight OK - %d page(s) -> %d chunk(s), %d section(s)",
        len(pages),
        len(chunks),
        len({c["section"] for c in chunks if c["section"]}),
    )
    logger.info("Atomic chunks: %d", sum(c.get("record_type") in {"table_row", "key_value"} for c in chunks))
    return True


# ---------------------------------------------------------------------------
# Step 1 - snapshot doc_id -> original filename BEFORE wiping
# ---------------------------------------------------------------------------


def snapshot_filenames() -> dict[str, str]:
    """Read document_id -> original filename out of the existing index.

    Must run before any delete: on disk files are named {document_id}{ext},
    so the human-readable filename only survives in metadata.
    """
    mapping: dict[str, str] = {}
    try:
        collection = ingestion._get_collection()
        results = collection.get(include=["metadatas"])
        for meta in results.get("metadatas", []) or []:
            if not meta:
                continue
            doc_id = meta.get("document_id")
            filename = meta.get("filename")
            if doc_id and filename:
                mapping[doc_id] = filename
    except Exception as e:
        logger.warning("Could not read ChromaDB metadata: %s", e)

    # Fill gaps from SQLite in case Chroma was already partially wiped.
    try:
        conn = get_sqlite_connection()
        for doc_id, filename in conn.execute(
            "SELECT DISTINCT document_id, filename FROM document_chunks"
        ):
            mapping.setdefault(doc_id, filename)
        conn.close()
    except Exception as e:
        logger.warning("Could not read SQLite metadata: %s", e)

    logger.info("Recovered %d filename mapping(s)", len(mapping))
    return mapping


# ---------------------------------------------------------------------------
# Step 2 - discover files on disk
# ---------------------------------------------------------------------------


def discover_files(mapping: dict[str, str]) -> list[tuple[str, Path, str]]:
    """Return (document_id, path, original_filename) for every stored file."""
    found: list[tuple[str, Path, str]] = []

    if not ingestion.UPLOAD_DIR.exists():
        logger.error("Upload dir does not exist: %s", ingestion.UPLOAD_DIR)
        return found

    for path in sorted(ingestion.UPLOAD_DIR.iterdir()):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext not in ingestion.ALLOWED_EXTENSIONS:
            continue
        doc_id = path.stem
        found.append((doc_id, path, mapping.get(doc_id, path.name)))

    orphans = set(mapping) - {d for d, _, _ in found}
    if orphans:
        logger.warning(
            "%d document(s) indexed but missing on disk (will be dropped): %s",
            len(orphans),
            ", ".join(sorted(orphans)[:5]),
        )
    return found


# ---------------------------------------------------------------------------
# Step 3 - wipe the old index
# ---------------------------------------------------------------------------


def wipe_index() -> None:
    """Drop the Chroma collection and clear the FTS5 table."""
    try:
        from shared.config import get_settings

        settings = get_settings()
        ingestion._get_chroma_client().delete_collection(
            name=settings.chroma_collection
        )
        logger.info("Dropped Chroma collection '%s'", settings.chroma_collection)
    except Exception as e:
        logger.warning("Chroma collection drop skipped: %s", e)

    # Recreate empty so hnsw:space metadata is preserved and downstream
    # services don't 404 mid-run.
    ingestion._get_collection()

    try:
        conn = get_sqlite_connection()
        conn.execute("DELETE FROM document_chunks")
        conn.commit()
        conn.close()
        logger.info("Cleared SQLite FTS5 table document_chunks")
    except Exception as e:
        logger.warning("SQLite clear skipped: %s", e)


def wipe_one(document_id: str) -> None:
    """Delete just one document's chunks from both stores."""
    try:
        collection = ingestion._get_collection()
        res = collection.get(where={"document_id": document_id}, include=[])
        if res["ids"]:
            collection.delete(ids=res["ids"])
            logger.info("Deleted %d old Chroma chunk(s)", len(res["ids"]))
    except Exception as e:
        logger.warning("Targeted Chroma delete failed: %s", e)

    try:
        conn = get_sqlite_connection()
        conn.execute(
            "DELETE FROM document_chunks WHERE document_id = ?", (document_id,)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("Targeted SQLite delete failed: %s", e)


# ---------------------------------------------------------------------------
# Step 4 - reprocess
# ---------------------------------------------------------------------------


async def reingest(targets: list[tuple[str, Path, str]]) -> tuple[int, int]:
    ok = failed = 0
    for n, (doc_id, path, original) in enumerate(targets, 1):
        doc_type = ingestion.ALLOWED_EXTENSIONS[path.suffix.lower()]
        logger.info("[%d/%d] %s (%s)", n, len(targets), original, doc_id)
        try:
            # Sequential on purpose: parallel embedding saturates Ollama.
            await ingestion._process_document(doc_id, path, original, doc_type)
            info = ingestion._documents.get(doc_id)
            status = ingestion._ingestion_tasks.get(doc_id)

            if status and status.status.value == "failed":
                logger.error("        -> FAILED: %s", status.error_message)
                failed += 1
            else:
                logger.info("        -> %s chunks", info.chunk_count if info else "?")
                ok += 1
        except Exception as e:
            logger.error("        -> FAILED: %s", e)
            failed += 1
    return ok, failed


async def amain() -> int:
    parser = argparse.ArgumentParser(description="Re-ingest all stored documents.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-vectors", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--only", metavar="DOCUMENT_ID")
    args = parser.parse_args()

    mapping = snapshot_filenames()
    targets = discover_files(mapping)

    if args.only:
        targets = [t for t in targets if t[0] == args.only]
        if not targets:
            logger.error("document_id %s not found in %s", args.only, ingestion.UPLOAD_DIR)
            return 1

    if not targets:
        logger.info("Nothing to re-ingest.")
        return 0

    logger.info("%d document(s) queued:", len(targets))
    for doc_id, path, original in targets:
        logger.info("  %s  %s  (%.1f KB)", doc_id, original, path.stat().st_size / 1024)

    if args.dry_run:
        logger.info("Dry run - no changes made.")
        return 0

    if not args.skip_preflight and not preflight(targets):
        logger.error("Aborting: index left untouched. Fix the parser, then rerun.")
        return 1

    if not args.keep_vectors:
        if args.only:
            wipe_one(args.only)
        else:
            wipe_index()

    ok, failed = await reingest(targets)

    try:
        await ingestion._get_embed_client().close()
    except Exception:
        pass

    try:
        logger.info("Index now holds %d chunk(s).", ingestion._get_collection().count())
    except Exception:
        pass

    logger.info("Done. %d succeeded, %d failed.", ok, failed)
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
