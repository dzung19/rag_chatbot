"""Ollama LLM client for text generation.

Supports both streaming and non-streaming generation via Ollama's /api/chat endpoint.
Supports structured messages (system/user separation), CPU inference optimizations,
and persistent HTTP connection pooling.
"""

from __future__ import annotations

import json
import logging
from typing import AsyncGenerator, Optional

import httpx  # type: ignore[import]

logger = logging.getLogger(__name__)


class OllamaLLMClient:
    """Async client for Ollama LLM inference."""

    def __init__(
        self,
        ollama_host: str = "http://ollama:11434",
        model: str = "gemma4:e4b",
        timeout: int = 300,
        num_threads: Optional[int] = None,
    ):
        self.ollama_host = ollama_host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.num_threads = num_threads
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=30.0),
                limits=httpx.Limits(
                    max_keepalive_connections=10,
                    max_connections=20,
                    keepalive_expiry=60.0,
                ),
            )
        return self._client

    def _build_options(
        self,
        temperature: float = 0.7,
        num_ctx: int = 8192,
        num_predict: int = 1024,
        repeat_penalty: float = 1.1,
        top_p: float = 0.9,
        top_k: int = 40,
    ) -> dict:
        """Construct CPU-optimized inference options for Ollama."""
        opts: dict = {
            "temperature": temperature,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
            "repeat_penalty": repeat_penalty,
            "top_p": top_p,
            "top_k": top_k,
        }
        if self.num_threads and self.num_threads > 0:
            opts["num_thread"] = self.num_threads
        return opts

    def _prepare_messages(
        self,
        prompt: Optional[str] = None,
        messages: Optional[list[dict]] = None,
    ) -> list[dict]:
        """Format input into role-based messages for Ollama /api/chat."""
        if messages is not None:
            return messages
        if prompt is not None:
            return [{"role": "user", "content": prompt}]
        raise ValueError("Either 'prompt' or 'messages' must be provided.")

    async def generate(
        self,
        prompt: Optional[str] = None,
        messages: Optional[list[dict]] = None,
        temperature: float = 0.7,
        num_predict: int = 1024,
    ) -> str:
        """Generate a complete (non-streaming) response.

        Args:
            prompt: Full prompt string (fallback if messages not supplied).
            messages: List of message dicts with role and content keys.
            temperature: Sampling temperature.
            num_predict: Maximum tokens to generate.

        Returns:
            Generated text response.
        """
        client = await self._get_client()
        chat_messages = self._prepare_messages(prompt=prompt, messages=messages)
        options = self._build_options(
            temperature=temperature,
            num_predict=num_predict,
        )

        response = await client.post(
            f"{self.ollama_host}/api/chat",
            json={
                "model": self.model,
                "messages": chat_messages,
                "stream": False,
                "options": options,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", "")

    async def generate_stream(
        self,
        prompt: Optional[str] = None,
        messages: Optional[list[dict]] = None,
        temperature: float = 0.7,
        num_predict: int = 1024,
    ) -> AsyncGenerator[str, None]:
        """Generate a streaming response, yielding tokens.

        Args:
            prompt: Full prompt string (fallback if messages not supplied).
            messages: List of message dicts with role and content keys.
            temperature: Sampling temperature.
            num_predict: Maximum tokens to generate.

        Yields:
            Individual tokens as they are generated.
        """
        client = await self._get_client()
        chat_messages = self._prepare_messages(prompt=prompt, messages=messages)
        options = self._build_options(
            temperature=temperature,
            num_predict=num_predict,
        )

        async with client.stream(
            "POST",
            f"{self.ollama_host}/api/chat",
            json={
                "model": self.model,
                "messages": chat_messages,
                "stream": True,
                "options": options,
            },
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    content = data.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if data.get("done", False):
                        logger.debug("Ollama stream completed.")
                        break
                except json.JSONDecodeError:
                    continue

    async def health_check(self) -> bool:
        """Check if Ollama is reachable and model is loaded."""
        try:
            client = await self._get_client()
            response = await client.get(
                f"{self.ollama_host}/api/tags",
                timeout=10.0,
            )
            return response.status_code == 200
        except Exception:
            return False

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
