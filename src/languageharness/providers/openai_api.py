"""OpenAI (or OpenAI-compatible) client. Responses API for official OpenAI, Chat Completions otherwise."""
from __future__ import annotations

from typing import AsyncIterator

import openai

from .base import Message, ProviderError, parse_json_loose


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, base_url: str | None = None, api_style: str = "auto", default_headers: dict | None = None,
                 stateless_stream_only: bool = False):
        self.client = openai.AsyncOpenAI(api_key=api_key, base_url=base_url, default_headers=default_headers, max_retries=1)
        self.style = ("chat" if base_url else "responses") if api_style == "auto" else api_style
        # ChatGPT-subscription tokens: Responses API only, store=false, always streamed.
        self.stateless = stateless_stream_only
        if self.stateless:
            self.style = "responses"

    def _extra(self) -> dict:
        return {"store": False} if self.stateless else {}

    async def _stream_responses(self, model: str, messages: list[Message], text_format: dict | None = None) -> AsyncIterator[str]:
        system, rest = self._split(messages)
        kw = {"text": {"format": text_format}} if text_format else {}
        stream = await self.client.responses.create(model=model, instructions=system or None, input=rest, stream=True, **self._extra(), **kw)
        async for ev in stream:
            if ev.type == "response.output_text.delta":
                yield ev.delta
            elif ev.type in ("response.failed", "error"):
                err = getattr(getattr(ev, "response", None), "error", None) or getattr(ev, "message", ev)
                raise ProviderError(f"OpenAI stream error: {err}")

    @staticmethod
    def _split(messages: list[Message]) -> tuple[str, list[Message]]:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        return system, [m for m in messages if m["role"] != "system"]

    async def stream_chat(self, model: str, messages: list[Message]) -> AsyncIterator[str]:
        try:
            if self.style == "responses":
                async for piece in self._stream_responses(model, messages):
                    yield piece
            else:
                stream = await self.client.chat.completions.create(model=model, messages=messages, stream=True)
                async for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        yield chunk.choices[0].delta.content
        except openai.APIStatusError as e:
            raise ProviderError(f"OpenAI {e.status_code}: {e.response.text[:300]}") from e
        except openai.OpenAIError as e:
            raise ProviderError(f"OpenAI: {e}") from e

    async def complete_json(self, model: str, messages: list[Message], schema: dict) -> dict:
        try:
            fmt = {"type": "json_schema", "name": "result", "schema": schema, "strict": False}
            if self.stateless:
                return parse_json_loose("".join([p async for p in self._stream_responses(model, messages, fmt)]))
            if self.style == "responses":
                system, rest = self._split(messages)
                r = await self.client.responses.create(
                    model=model,
                    instructions=system or None,
                    input=rest,
                    text={"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": False}},
                )
                return parse_json_loose(r.output_text)
            r = await self.client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_schema", "json_schema": {"name": "result", "schema": schema}},
            )
            return parse_json_loose(r.choices[0].message.content or "")
        except openai.OpenAIError as e:
            raise ProviderError(f"OpenAI: {e}") from e

    async def list_models(self) -> list[str]:
        try:
            page = await self.client.models.list()
            return sorted(m.id for m in page.data)
        except openai.OpenAIError:
            return []
