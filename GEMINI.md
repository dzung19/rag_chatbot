# RAG Chatbot — Project Context

## Overview
A company-internal **Retrieval-Augmented Generation (RAG) chatbot** that allows employees to query company documents using natural language. The system ingests documents (PDF, DOCX, PPTX, XLSX, TXT, Markdown), stores them as vector embeddings, and uses a local LLM to generate grounded answers with source citations.

## Architecture
**Microservice-based** architecture using **FastAPI** (Python), orchestrated with **Docker Compose**.

### Services
| Service | Port | Description |
|---------|------|-------------|
| Web UI | 3000 | Browser-based chat interface, document management, log viewer |
| API Gateway | 8000 | Request routing, authentication, rate limiting, security headers |
| Document Ingestion | 8001 | Document parsing, chunking, embedding, storage |
| RAG Query Engine | 8002 | Context retrieval, prompt construction, LLM streaming |
| Ollama (LLM) | 11434 | Local LLM inference — Gemma 4 E4B/Qwen 3.5 (CPU-only) |
| ChromaDB | 8003 | Vector database for document embeddings |
| Loki | 3100 | Centralized log aggregation |
| Promtail | 9080 | Log shipping agent (Docker → Loki) |
| Grafana | 3001 | Log analysis dashboards and alerting |

### Tech Stack
- **Backend**: Python 3.12, FastAPI, Pydantic v2, httpx (async HTTP)
- **LLM**: Gemma 4 E4B via Ollama (CPU inference, ~15-25 tok/s on i9-14900)
- **Embeddings**: nomic-embed-text via Ollama
- **Vector Store**: ChromaDB (persistent, Docker volume)
- **Document Parsing**: PyMuPDF (PDF), python-docx (DOCX), python-pptx (PPTX), openpyxl (XLSX)
- **Text Splitting**: langchain-text-splitters (RecursiveCharacterTextSplitter)
- **Streaming**: SSE (Server-Sent Events) via sse-starlette
- **Frontend**: Vanilla HTML/CSS/JS, served via Nginx
- **Logging**: Structured JSON → Loki + Promtail + Grafana
- **Deployment**: Docker Compose

### Data Flow
```
Upload: File → Gateway → Ingestion → Parse → Chunk → Embed (Ollama) → Store (ChromaDB)
Query:  Question → Gateway → RAG Engine → Embed query → Search ChromaDB → Build prompt → LLM (Ollama) → Stream answer
```

## Getting Started

### Prerequisites
- Docker & Docker Compose
- 32GB+ RAM recommended (CPU-only LLM inference)

### Quick Start
```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env with your API_KEY

# 2. Launch all services
docker-compose up --build

# 3. Access the app
# Web UI:    http://localhost:3000
# API Docs:  http://localhost:8000/docs
# Grafana:   http://localhost:3001 (admin/admin)
```

### Environment Variables
See `.env.example` for all configurable options. Key variables:
- `API_KEY` — Authentication key for API access
- `OLLAMA_MODEL` — LLM model (default: `gemma4:e4b`)
- `CHUNK_SIZE` / `CHUNK_OVERLAP` — Text splitting parameters
- `LOG_LEVEL` — Logging verbosity (DEBUG, INFO, WARNING, ERROR)

## Project Structure
```
rag_chatbot/
├── GEMINI.md                 # This file
├── docker-compose.yml        # Service orchestration
├── .env.example              # Environment template
├── shared/                   # Shared code (models, config, security, logging)
├── services/
│   ├── gateway/              # API Gateway
│   ├── ingestion/            # Document Ingestion
│   ├── rag_engine/           # RAG Query Engine
│   ├── web_ui/               # Frontend (HTML/CSS/JS + Nginx)
│   └── onedrive_connector/   # OneDrive/SharePoint (Phase 2)
└── logging/                  # Loki, Promtail, Grafana config
```

## Security Notes
- API key authentication on all endpoints
- Rate limiting per API key
- File upload validation (extension allow-list, magic bytes, size limit, UUID rename)
- Strict CORS, CSP, and security headers
- No hardcoded secrets — environment variable resolution with fallback
- Structured logging with sensitive data filtering
- TODO(security): OAuth/SSO for production, malware scanning, CDR stripping
