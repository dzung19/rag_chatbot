"""Patch existing ingestion main.py; keep original as main.py.bak.
Usage: python install_compare.py /path/to/ingestion/main.py
"""
from pathlib import Path
import sys
if len(sys.argv) != 2: raise SystemExit("Usage: python install_compare.py /path/to/ingestion/main.py")
p = Path(sys.argv[1]); source = p.read_text(encoding="utf-8")
if "install_compare_router(_documents, UPLOAD_DIR)" in source or '@app.get("/ingest/compare")' in source:
    raise SystemExit("Comparison endpoint already installed; no changes made")
anchor = '@app.get("/ingest/health"'
if anchor not in source or "_documents: dict" not in source or "UPLOAD_DIR" not in source:
    raise SystemExit("Unexpected ingestion main.py; unchanged")
patch = "from compare_router import install as install_compare_router\napp.include_router(install_compare_router(_documents, UPLOAD_DIR))\n\n"
updated = source.replace(anchor, patch + anchor, 1)
compile(updated, str(p), "exec")
p.with_suffix(p.suffix + ".bak").write_text(source, encoding="utf-8")
p.write_text(updated, encoding="utf-8")
print("Patched", p)
