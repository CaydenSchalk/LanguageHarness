"""Prioritize known Anki items and pick topic-relevant targets for a session."""
from __future__ import annotations

import math
import time

from ..prompting import render
from .base import KnownItem


def score(item: KnownItem, now: float | None = None) -> float:
    now = now or time.time()
    s = 1.0 if item.status == "mature" else 0.6
    if item.last_review:
        days = max(0.0, (now - item.last_review) / 86400)
        s += math.exp(-days / 14)  # recently reviewed -> fresh in memory, good to activate
    s += 0.15 * min(item.lapses, 5) / 5  # some reinforcement for leeches
    return s


def prioritize(items: list[KnownItem], limit: int, now: float | None = None) -> list[KnownItem]:
    return sorted(items, key=lambda i: score(i, now), reverse=True)[:limit]


SELECT_SCHEMA = {
    "type": "object",
    "properties": {"indexes": {"type": "array", "items": {"type": "integer"}}},
    "required": ["indexes"],
}


async def pick_targets(provider, model: str, items: list[KnownItem], *, topic: str, scenario: str, level: str,
                       count: int, candidate_limit: int) -> list[KnownItem]:
    cands = prioritize(items, candidate_limit)
    if len(cands) <= count:
        return cands
    lines = "\n".join(f"{i} | {c.expression} | {c.reading} | {c.meaning} | {c.status}" for i, c in enumerate(cands))
    prompt = render("select-words.md", topic=topic, scenario=scenario or "-", level=level, count=count, candidates=lines)
    try:
        out = await provider.complete_json(model, [{"role": "user", "content": prompt}], SELECT_SCHEMA)
        picked, seen = [], set()
        for i in out.get("indexes", []):
            if isinstance(i, int) and 0 <= i < len(cands) and i not in seen:
                seen.add(i)
                picked.append(cands[i])
        if picked:
            return picked[:count]
    except Exception:  # noqa: BLE001 - fall back to score order
        pass
    return cands[:count]
