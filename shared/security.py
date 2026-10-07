"""Security utilities for RAG Chatbot services.

Provides API key validation, rate limiting, and security headers middleware.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections import defaultdict
from typing import Callable

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from shared.config import get_settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------

from dataclasses import dataclass



@dataclass(frozen=True)
class CurrentUser:
    user_id: str


def get_current_user() -> CurrentUser:
    return CurrentUser(
        user_id="local-dev-user",
    )

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def validate_api_key(api_key: str | None = Depends(_api_key_header)) -> str:
    """Validate the API key from the X-API-Key header.

    Uses constant-time comparison to prevent timing attacks.
    """
    if api_key is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide it via the X-API-Key header.",
        )
    settings = get_settings()
    # Constant-time comparison
    if not hmac.compare_digest(api_key.encode(), settings.api_key.encode()):
        # Log the attempt but NOT the key value itself
        logger.warning("Invalid API key attempt from request.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid API key.",
        )
    return api_key


# ---------------------------------------------------------------------------
# Rate Limiting Middleware (Token-Bucket per API key)
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Simple in-memory token-bucket rate limiter keyed by API key.

    For production with multiple instances, replace with Redis-backed limiter.
    """

    def __init__(
        self,
        app: ASGIApp,
        max_tokens: int = 60,
        refill_rate: float = 1.0,  # tokens per second
    ):
        super().__init__(app)
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self._buckets: dict[str, dict] = defaultdict(
            lambda: {"tokens": max_tokens, "last_refill": time.monotonic()}
        )

    async def dispatch(self, request: Request, call_next: Callable):
        api_key = request.headers.get("X-API-Key", "anonymous")
        # Hash the key for storage so we don't hold raw keys in memory
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:16]

        bucket = self._buckets[key_hash]
        now = time.monotonic()
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(
            self.max_tokens, bucket["tokens"] + elapsed * self.refill_rate
        )
        bucket["last_refill"] = now

        if bucket["tokens"] < 1:
            logger.warning("Rate limit exceeded for key hash %s", key_hash)
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded. Please try again later."},
            )

        bucket["tokens"] -= 1
        return await call_next(request)


# ---------------------------------------------------------------------------
# Security Headers Middleware
# ---------------------------------------------------------------------------

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "frame-ancestors 'none'"
        )
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-DNS-Prefetch-Control"] = "off"
        return response


# ---------------------------------------------------------------------------
# Prompt Injection Detection
# ---------------------------------------------------------------------------

# Blocklist of common prompt injection attack phrases (English and Vietnamese)
PROMPT_INJECTION_BLOCKLIST = [
    "ignore previous instructions",
    "ignore all previous instructions",
    "forget all previous",
    "system prompt",
    "bỏ qua các hướng dẫn",
    "bỏ qua hướng dẫn",
    "quên đi các lệnh",
    "bạn là một",
    "you are now a",
    "you are a",
    "từ giờ bạn là",
    "print previous",
    "in ra hướng dẫn",
    "hiển thị hướng dẫn",
]

def detect_prompt_injection(query: str) -> bool:
    """Detect if a user query contains potential prompt injection attacks.
    
    Returns True if an injection is detected, False otherwise.
    """
    if not query:
        return False
        
    query_lower = query.lower()
    for phrase in PROMPT_INJECTION_BLOCKLIST:
        if phrase in query_lower:
            logger.warning("Prompt injection detected! Matched phrase: '%s'", phrase)
            return True
            
    return False
