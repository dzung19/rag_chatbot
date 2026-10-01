# BL Comparison Skill Integration

Copy these files into `services/rag_engine`:

- `skill_loader.py`
- `pdf_compare_tool.py` (replace the current file)
- `skills/compare_bl_checklist/SKILL.md`
- `skills/compare_bl_checklist/config.json`

No change is required in the current `main.py`: it already calls `prepare_comparison()` before normal RAG.

## Trigger

The skill activates only when:

1. exactly two document IDs are selected; and
2. the user query contains an explicit configured comparison term.

Examples: `Compare these two BL files`, `Đối chiếu BL với BL Check List`, `So sánh hai file`.

Ordinary questions continue through normal RAG.

## Runtime configuration

For the current Windows-local setup:

```powershell
$env:INGESTION_URL = "http://127.0.0.1:8001"
```

Restart the RAG service after copying the files.

## Important functional limitation

The current `/ingest/compare` endpoint returns normalized field/value matches. It does not yet provide the complete structured evidence required by the skill for invoice grouping, G-TOTAL validation, per-container descriptions, continuation markers, BL-vs-checklist role identification, or workbook creation. The skill therefore instructs the model to return `NOT FOUND` or `NOT UNDERSTOOD` whenever that evidence is absent.

The instruction's `.xlsx` requirement needs a separate report-generation action and downloadable artifact endpoint. Do not tell users that an Excel file was created until such an endpoint actually returns a real file.
