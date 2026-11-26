"""Provider abstraction: one streaming chat call and one structured JSON call."""
from __future__ import annotations

import json
import re
from typing import AsyncIterator, Protocol

Message = dict  # {"role": "system" | "user" | "assistant", "content": str}


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    name: str

    def stream_chat(self, model: str, messages: list[Message]) -> AsyncIterator[str]: ...

    async def complete_json(self, model: str, messages: list[Message], schema: dict) -> dict: ...

    async def list_models(self) -> list[str]: ...


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json_loose(text: str) -> dict:
    """Parse model output as JSON, tolerating code fences and leading chatter."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    stripped = _FENCE.sub("", text).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ProviderError(f"Model did not return valid JSON: {text[:200]!r}")


def get_provider(name: str, settings=None) -> Provider:
    from ..config import load_settings

    settings = settings or load_settings()
    if name == "ollama":
        from .ollama import OllamaProvider

        return OllamaProvider(settings.ollama_base_url, num_ctx=settings.ollama_num_ctx, think=settings.ollama_think)
    if name == "openai":
        from ..config import get_secret
        from .openai_api import OpenAIProvider

        key = get_secret("OPENAI_API_KEY")
        if not key:
            raise ProviderError("No OpenAI API key set. Add one in Settings.")
        return OpenAIProvider(api_key=key, base_url=settings.openai_base_url or None, api_style=settings.openai_api_style)
    if name == "chatgpt":
        from .chatgpt_oauth import ChatGPTProvider

        return ChatGPTProvider()
    raise ProviderError(f"Unknown provider {name!r}")
