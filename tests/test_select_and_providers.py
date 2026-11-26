import json
import time

import httpx

from conftest import FakeProvider, run
from languageharness.anki.base import KnownItem
from languageharness.anki.select import pick_targets, prioritize
from languageharness.providers.base import parse_json_loose
from languageharness.providers.ollama import OllamaProvider


def test_prioritize_prefers_mature_and_recent():
    now = time.time()
    items = [
        KnownItem(card_id=1, expression="old young", interval=3, last_review=now - 90 * 86400),
        KnownItem(card_id=2, expression="recent mature", interval=40, last_review=now - 86400),
        KnownItem(card_id=3, expression="old mature", interval=40, last_review=now - 90 * 86400),
    ]
    assert [i.expression for i in prioritize(items, 3, now)] == ["recent mature", "old mature", "old young"]


def test_pick_targets_uses_model_and_falls_back():
    items = [KnownItem(card_id=i, expression=f"w{i}", interval=30 - i) for i in range(10)]
    p = FakeProvider()
    picked = run(pick_targets(p, "m", items, topic="t", scenario="", level="l", count=3, candidate_limit=10))
    assert [i.expression for i in picked] == ["w0", "w1"]

    class Broken(FakeProvider):
        async def complete_json(self, *a):
            raise RuntimeError("boom")

    picked = run(pick_targets(Broken(), "m", items, topic="t", scenario="", level="l", count=3, candidate_limit=10))
    assert len(picked) == 3


def test_parse_json_loose():
    assert parse_json_loose('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Sure! {"a": [1]} done') == {"a": [1]}


def test_ollama_stream_and_json():
    def handler(req):
        body = json.loads(req.content)
        if body["stream"]:
            lines = [{"message": {"content": "こん"}}, {"message": {"content": "にちは"}}, {"done": True}]
            return httpx.Response(200, text="\n".join(json.dumps(l, ensure_ascii=False) for l in lines))
        assert body["format"]["type"] == "object"
        return httpx.Response(200, json={"message": {"content": '{"x": 1}'}})

    p = OllamaProvider(transport=httpx.MockTransport(handler))

    async def go():
        return "".join([c async for c in p.stream_chat("m", [])]), await p.complete_json("m", [], {"type": "object"})

    assert run(go()) == ("こんにちは", {"x": 1})
