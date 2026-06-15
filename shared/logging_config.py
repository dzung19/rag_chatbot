"""Structured JSON logging configuration for RAG Chatbot services.

Outputs JSON-formatted logs to stdout (captured by Docker → Promtail → Loki).
Sensitive data is filtered from log records.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

import contextvars

# Context variable to store the current request ID
request_id_ctx = contextvars.ContextVar("request_id", default=None)



# ---------------------------------------------------------------------------
# Sensitive Data Filter
# ---------------------------------------------------------------------------

# Patterns to redact from log messages
_SENSITIVE_PATTERNS = [
    re.compile(r'("?(?:password|passwd|secret|token|api_key|apikey|authorization|api[-_]?key)"?\s*[:=]\s*)"[^"]*"', re.IGNORECASE),
    re.compile(r'("?(?:password|passwd|secret|token|api_key|apikey|authorization|api[-_]?key)"?\s*[:=]\s*)\S+', re.IGNORECASE),
]


def _redact_sensitive(message: str) -> str:
    """Redact sensitive values from a log message."""
    for pattern in _SENSITIVE_PATTERNS:
        message = pattern.sub(r'\1"[REDACTED]"', message)
    return message


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------

class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def __init__(self, service_name: str = "unknown"):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "service": self.service_name,
            "logger": record.name,
            "message": _redact_sensitive(record.getMessage()),
        }
        # Add request_id if present (either directly on record or from context)
        req_id = getattr(record, "request_id", None) or request_id_ctx.get()
        if req_id:
            log_entry["request_id"] = req_id

        # Add extra fields (but filter sensitive keys)
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            safe_extra = {
                k: v
                for k, v in record.extra_data.items()
                if k.lower() not in {"password", "secret", "token", "api_key", "authorization"}
            }
            if safe_extra:
                log_entry["extra"] = safe_extra

        # Add exception info if present
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def setup_logging(service_name: str, log_level: str = "INFO") -> None:
    """Configure structured JSON logging for a service.

    Args:
        service_name: Name of the microservice (e.g., "gateway", "ingestion").
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR).
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add JSON handler to stdout
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter(service_name=service_name))
    root_logger.addHandler(handler)

    # Quiet noisy libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("chromadb").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Request Logging Middleware
# ---------------------------------------------------------------------------

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with timing, status, and a unique request ID."""

    def __init__(self, app: ASGIApp, service_name: str = "unknown"):
        super().__init__(app)
        self.service_name = service_name
        self.logger = logging.getLogger(f"{service_name}.requests")

    async def dispatch(self, request: Request, call_next: Callable):
        request_id = str(uuid.uuid4())[:8]
        request.state.request_id = request_id

        # Set request_id in context variable
        token = request_id_ctx.set(request_id)
        start_time = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start_time) * 1000
            self.logger.error(
                "Request failed: %s %s [%s] %.1fms",
                request.method,
                request.url.path,
                request_id,
                duration_ms,
                exc_info=True,
            )
            raise
        finally:
            # Always reset the context variable
            request_id_ctx.reset(token)

        duration_ms = (time.perf_counter() - start_time) * 1000
        self.logger.info(
            "%s %s → %d [%s] %.1fms",
            request.method,
            request.url.path,
            response.status_code,
            request_id,
            duration_ms,
        )
        response.headers["X-Request-ID"] = request_id
        return response
