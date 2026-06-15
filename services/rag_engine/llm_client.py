"""Ollama LLM client for text generation.

Supports both streaming and non-streaming generation via Ollama's /api/chat endpoint.
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
    ):
        self.ollama_host = ollama_host.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=30.0)
            )
        return self._client

    async def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
    ) -> str:
        """Generate a complete (non-streaming) response.

        Args:
            prompt: Full prompt string including context.
            temperature: Sampling temperature.

        Returns:
            Generated text response.
        """
        client = await self._get_client()

        response = await client.post(
            f"{self.ollama_host}/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_ctx": 8192,
                },
            },
        )
        response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", "")

    async def generate_stream(
        self,
        prompt: str,
        temperature: float = 0.7,
    ) -> AsyncGenerator[str, None]:
        """Generate a streaming response, yielding tokens.

        Args:
            prompt: Full prompt string including context.
            temperature: Sampling temperature.

        Yields:
            Individual tokens as they are generated.
        """
        client = await self._get_client()

        async with client.stream(
            "POST",
            f"{self.ollama_host}/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": True,
                "options": {
                    "temperature": temperature,
                    "num_ctx": 8192,
                },
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
                        logger.info("Ollama stream done.")
                        break
                except json.JSONDecodeError:
                    continue
        logger.info("Ollama generate_stream completed.")

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
