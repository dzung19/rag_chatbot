"""Chunking inspection & evaluation tool.

Two modes:

  inspect  - parse a real document, run it through the chunker, and print
             the chunks plus health metrics. Needs NO Chroma and NO Ollama,
             so you can iterate on chunker.py in seconds.

  eval     - run a list of questions against the live retrieval index and
             report hit-rate. Needs the stack up.

Usage:
    python inspect_chunks.py inspect uploaded_docs/<file>.pdf
    python inspect_chunks.py inspect <file>.pdf --full --json out.json
    python inspect_chunks.py eval questions.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from chunker import chunk_pages  # noqa: E402
from document_parser import parse_document  # noqa: E402

# Warning thresholds — tune to taste.
TINY_CHUNK = 100        # chunks this small rarely carry a usable answer
HUGE_CHUNK_RATIO = 1.15  # chunk_size overshoot that signals a bad separator
ORPHAN_SECTION_RATIO = 0.4  # share of chunks with no detected section


def _pct(n: int, total: int) -> str:
    return f"{(100 * n / total):.0f}%" if total else "0%"


def inspect(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        return 1

    ext = path.suffix.lower().lstrip(".")
    pages = list(parse_document(str(path), ext))
    if not pages:
        print("No text extracted — the parser, not the chunker, is the problem.")
        return 1

    chunks = chunk_pages(
        pages,
        source=path.name,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        parent_size=args.parent_size,
    )
    if not chunks:
        print("Parser produced text but chunker produced nothing.")
        return 1

    lengths = [len(c["text"]) for c in chunks]
    sections = {c["section"] for c in chunks if c["section"]}
    no_section = sum(1 for c in chunks if not c["section"])
    tiny = [c for c in chunks if len(c["text"]) < TINY_CHUNK]
    huge = [c for c in chunks if c.get("record_type", "prose") == "prose"
            and len(c["text"]) > args.chunk_size * HUGE_CHUNK_RATIO]
    atomic = [c for c in chunks if c.get("record_type") in {"table_row", "key_value"}]
    parents = {c["parent_index"] for c in chunks}
    pages_seen = {c["page"] for c in chunks}

    print(f"\n=== {path.name} ===")
    print(f"pages parsed    : {len(pages)}")
    print(f"chunks          : {len(chunks)}")
    print(f"parent blocks   : {len(parents)}")
    print(f"atomic records  : {len(atomic)}")
    print(f"children/parent : {len(chunks) / max(1, len(parents)):.1f}")
    print(f"sections found  : {len(sections)}")
    print(
        "chunk chars     : "
        f"min={min(lengths)} median={int(statistics.median(lengths))} "
        f"mean={int(statistics.mean(lengths))} max={max(lengths)}"
    )
    print(f"pages covered   : {min(pages_seen)}..{max(pages_seen)}")

    print("\n--- health checks ---")
    problems = 0

    if len(pages_seen) == 1 and len(pages) > 1:
        print("FAIL page tracking: every chunk landed on one page")
        problems += 1
    else:
        print("ok   page tracking")

    if not sections:
        print("WARN no sections detected — headings unrecognised, or a flat doc")
        problems += 1
    elif no_section / len(chunks) > ORPHAN_SECTION_RATIO:
        print(
            f"WARN {_pct(no_section, len(chunks))} of chunks have no section "
            "— heading patterns may not match this document's style"
        )
        problems += 1
    else:
        print(f"ok   section coverage ({_pct(len(chunks) - no_section, len(chunks))})")

    if tiny:
        print(f"WARN {len(tiny)} chunk(s) under {TINY_CHUNK} chars (fragments)")
        problems += 1
    else:
        print("ok   no fragment chunks")

    if huge:
        print(f"WARN {len(huge)} chunk(s) exceed chunk_size — no separator matched")
        problems += 1
    else:
        print("ok   no oversized chunks")

    if len(parents) == len(chunks):
        print("WARN every parent has exactly 1 child — parent/child adds nothing here")
        problems += 1
    else:
        print("ok   parent/child grouping active")

    # Mid-sentence truncation: a chunk that ends without terminal punctuation
    # and whose successor starts lowercase = a cut through a sentence.
    broken = 0
    for a, b in zip(chunks, chunks[1:]):
        if a["parent_index"] != b["parent_index"]:
            continue
        if a["text"][-1:] not in ".!?:;" and b["text"][:1].islower():
            broken += 1
    if broken:
        print(f"WARN {broken} boundary(ies) cut mid-sentence ({_pct(broken, len(chunks))})")
    else:
        print("ok   no mid-sentence cuts within parents")

    print(f"\n{problems} warning(s).")

    limit = len(chunks) if args.full else min(args.show, len(chunks))
    print(f"\n--- first {limit} chunk(s) ---")
    for c in chunks[:limit]:
        body = c["text"] if args.full else c["text"][:220].replace("\n", " ⏎ ")
        print(
            f"\n[{c['chunk_index']}] p{c['page']} | parent {c['parent_index']} "
            f"| {len(c['text'])} chars | section: {c['section'] or '-'}"
        )
        print(f"  embed→ {c['embed_text'][: len(c['embed_text']) - len(c['text'])]!r}")
        print(f"  {body}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nWrote {len(chunks)} chunks to {args.json}")

    return 0


def evaluate(args: argparse.Namespace) -> int:
    """Retrieval hit-rate against the live index.

    questions file: one JSON object per line
        {"q": "Thời gian bảo hành là bao lâu?", "expect": "12 tháng"}
    'expect' is a substring that must appear in at least one retrieved chunk.
    """
    import asyncio

    from embeddings import EmbeddingClient
    from shared.config import get_settings

    import main as ingestion

    cases = [
        json.loads(line)
        for line in Path(args.questions).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    async def run() -> int:
        settings = get_settings()
        client = EmbeddingClient(
            ollama_host=settings.ollama_host, model=settings.ollama_embed_model
        )
        collection = ingestion._get_collection()
        hits = 0

        for case in cases:
            vec = await client.embed_single(case["q"])
            res = collection.query(query_embeddings=[vec], n_results=args.k)
            docs = res["documents"][0]
            metas = res["metadatas"][0]
            rank = next(
                (i + 1 for i, d in enumerate(docs) if case["expect"].lower() in d.lower()),
                None,
            )
            if rank:
                hits += 1
                print(f"HIT  @{rank}  {case['q']}")
            else:
                print(f"MISS       {case['q']}")
                for d, m in zip(docs[: args.k], metas[: args.k]):
                    print(
                        f"       p{m.get('page')} {m.get('section') or '-'}: "
                        f"{d[:90]}..."
                    )
            await asyncio.sleep(0)

        await client.close()
        print(f"\nhit-rate@{args.k}: {hits}/{len(cases)} ({_pct(hits, len(cases))})")
        return 0

    return asyncio.run(run())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_i = sub.add_parser("inspect", help="offline chunk inspection")
    p_i.add_argument("file")
    p_i.add_argument("--chunk-size", type=int, default=1000)
    p_i.add_argument("--chunk-overlap", type=int, default=200)
    p_i.add_argument("--parent-size", type=int, default=2400)
    p_i.add_argument("--show", type=int, default=5)
    p_i.add_argument("--full", action="store_true", help="print every chunk in full")
    p_i.add_argument("--json", metavar="PATH", help="dump all chunks to JSON")
    p_i.set_defaults(func=inspect)

    p_e = sub.add_parser("eval", help="retrieval hit-rate (needs the stack up)")
    p_e.add_argument("questions", help="JSONL file of {q, expect}")
    p_e.add_argument("-k", type=int, default=5)
    p_e.set_defaults(func=evaluate)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
