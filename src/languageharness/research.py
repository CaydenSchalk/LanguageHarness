"""Topic pre-brief: Tavily web search -> structured brief from the utility model."""
from __future__ import annotations

import httpx

from .config import get_secret
from .prompting import render

TAVILY_URL = "https://api.tavily.com/search"

QUERIES_SCHEMA = {
    "type": "object",
    "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
    "required": ["queries"],
}

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "facts": {"type": "array", "items": {"type": "string"}},
        "vocab": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"word": {"type": "string"}, "reading": {"type": "string"}, "meaning": {"type": "string"}},
                "required": ["word", "reading", "meaning"],
            },
        },
        "arc": {"type": "array", "items": {"type": "string"}},
        "goal_checkpoints": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "facts", "vocab", "arc", "goal_checkpoints"],
}


async def tavily_search(query: str, max_results: int = 4, transport=None) -> list[dict]:
    key = get_secret("TAVILY_API_KEY")
    if not key:
        return []
    async with httpx.AsyncClient(timeout=30, transport=transport) as c:
        r = await c.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {key}"},
            json={"query": query, "max_results": max_results, "search_depth": "basic", "include_raw_content": True},
        )
    if r.status_code != 200:
        return []
    out = []
    for res in r.json().get("results", []):
        body = (res.get("raw_content") or res.get("content") or "")[:3000]
        out.append({"title": res.get("title", ""), "url": res.get("url", ""), "content": body})
    return out


async def make_queries(provider, model: str, topic: str, scenario: str) -> list[str]:
    prompt = (
        "Write 3 web search queries to research this conversation topic so a conversation partner can talk about it "
        "knowledgeably and accurately. Mix Japanese and English queries. Include one query about recent news if relevant.\n"
        f"Topic: {topic}\nScenario: {scenario or '-'}"
    )
    try:
        out = await provider.complete_json(model, [{"role": "user", "content": prompt}], QUERIES_SCHEMA)
        qs = [q for q in out.get("queries", []) if isinstance(q, str) and q.strip()]
        return qs[:4] or [topic]
    except Exception:  # noqa: BLE001
        return [topic]


async def build_brief(provider, model: str, *, topic: str, scenario: str, level: str, mode: str, goal: str,
                      search: bool = True, max_results: int = 4, progress=None) -> dict:
    sources: list[dict] = []
    if search and get_secret("TAVILY_API_KEY"):
        queries = await make_queries(provider, model, topic, scenario)
        for q in queries:
            if progress:
                await progress(f"検索中: {q}")
            for res in await tavily_search(q, max_results):
                if res["url"] not in {s["url"] for s in sources}:
                    sources.append(res)
    research = "\n\n".join(f"[{i+1}] {s['title']} ({s['url']})\n{s['content']}" for i, s in enumerate(sources[:12])) or "(no web results; rely on your own knowledge, and avoid uncertain specifics)"
    if mode == "goal":
        goal_line = f"\nConversation goal: {goal}"
        goal_instr = "3–5 concrete, observable checkpoints (in English) the learner must accomplish in the conversation to meet the goal."
    else:
        goal_line, goal_instr = "", "an empty list (no goal mode)."
    if progress:
        await progress("資料をまとめています…")
    prompt = render("brief.md", topic=topic, scenario=scenario or "-", level=level, mode=mode, goal_line=goal_line,
                    goal_instr=goal_instr, research=research)
    brief = await provider.complete_json(model, [{"role": "user", "content": prompt}], BRIEF_SCHEMA)
    for k in ("facts", "vocab", "arc", "goal_checkpoints"):
        brief.setdefault(k, [])
    brief.setdefault("summary", "")
    if mode != "goal":
        brief["goal_checkpoints"] = []
    brief["sources"] = [{"title": s["title"], "url": s["url"]} for s in sources[:12]]
    return brief
