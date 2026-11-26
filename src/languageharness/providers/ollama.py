"""Ollama native /api/chat client."""
from __future__ import annotations

import json
from typing import AsyncIterator

import httpx

from .base import Message, ProviderError, parse_json_loose


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434", transport: httpx.AsyncBaseTransport | None = None, num_ctx: int = 16384, think: bool | None = None):
        self.base_url = base_url.rstrip("/")
        self.num_ctx = num_ctx
        self.think = think
        self._transport = transport

    def _client(self, timeout: float = 300) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=self._transport)

    async def stream_chat(self, model: str, messages: list[Message]) -> AsyncIterator[str]:
        payload = {"model": model, "messages": messages, "stream": True, "options": {"num_ctx": self.num_ctx}}
        self._think(payload)
        try:
            async with self._client() as c, c.stream("POST", "/api/chat", json=payload) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="replace")
                    raise ProviderError(f"Ollama error {r.status_code}: {body[:300]}")
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = json.loads(line)
                    if chunk.get("error"):
                        raise ProviderError(f"Ollama: {chunk['error']}")
                    piece = (chunk.get("message") or {}).get("content") or ""
                    if piece:
                        yield piece
                    if chunk.get("done"):
                        break
        except httpx.ConnectError as e:
            raise ProviderError(f"Cannot reach Ollama at {self.base_url}. Is `ollama serve` running?") from e

    async def complete_json(self, model: str, messages: list[Message], schema: dict) -> dict:
        payload = {"model": model, "messages": messages, "stream": False, "format": schema, "options": {"temperature": 0.2, "num_ctx": self.num_ctx, "num_predict": 4096}}
        self._think(payload)
        try:
            async with self._client() as c:
                r = await c.post("/api/chat", json=payload)
        except httpx.ConnectError as e:
            raise ProviderError(f"Cannot reach Ollama at {self.base_url}. Is `ollama serve` running?") from e
        if r.status_code != 200:
            raise ProviderError(f"Ollama error {r.status_code}: {r.text[:300]}")
        return parse_json_loose(r.json()["message"]["content"])

    def _think(self, payload: dict) -> None:
        if self.think is not None and self._supports_think(payload["model"]):
            payload["think"] = self.think

    @staticmethod
    def _supports_think(model: str) -> bool:
        # Ollama rejects `think` for models without thinking support
        return any(k in model.lower() for k in ("qwen3", "deepseek-r1", "magistral", "phi4-reasoning"))

    async def list_models(self) -> list[str]:
        try:
            async with self._client(timeout=5) as c:
                r = await c.get("/api/tags")
            r.raise_for_status()
            return sorted(m["name"] for m in r.json().get("models", []))
        except httpx.HTTPError:
            return []
