import asyncio

import pytest

from languageharness import config


class FakeProvider:
    """Scripted provider: partner replies from a list; JSON calls answered by schema shape."""

    name = "fake"

    def __init__(self):
        self.replies = []
        self.chat_calls = []
        self.json_calls = []
        self.correction = None

    async def stream_chat(self, model, messages):
        self.chat_calls.append(messages)
        text = self.replies.pop(0) if self.replies else "そうですね。"
        for ch in [text[: len(text) // 2], text[len(text) // 2 :]]:
            yield ch

    async def complete_json(self, model, messages, schema):
        self.json_calls.append((messages, schema))
        props = schema["properties"]
        if "queries" in props:
            return {"queries": ["ラーメン 文化"]}
        if "arc" in props:
            return {"summary": "Ramen is popular.", "facts": ["Tonkotsu is from Fukuoka."], "vocab": [{"word": "豚骨", "reading": "とんこつ", "meaning": "pork bone"}],
                    "arc": ["greet", "favourite ramen", "regional styles", "close"], "goal_checkpoints": ["order ramen", "ask the price"]}
        if "indexes" in props:
            return {"indexes": [0, 1]}
        if "corrected" in props:
            return self.correction or {"ok": True, "corrected": "", "notes": [], "target_words_used": [], "goal_checkpoints_met": []}
        if "recurring_mistakes" in props:
            return {"summary": "Good.", "recurring_mistakes": [], "strengths": ["fluent"],
                    "new_words": [{"word": "替え玉", "reading": "かえだま", "meaning": "noodle refill", "example": "替え玉お願いします"}], "focus_next": ["particles"]}
        raise AssertionError(f"unexpected schema {props}")

    async def list_models(self):
        return ["fake"]


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    return tmp_path


@pytest.fixture
def fake(monkeypatch, data_dir):
    from languageharness import corrector, review, session

    p = FakeProvider()
    for mod in (session, corrector, review):
        monkeypatch.setattr(mod, "get_provider", lambda name, *a, **k: p)
    return p


def run(coro):
    return asyncio.run(coro)


async def collect(agen):
    return [ev async for ev in agen]
