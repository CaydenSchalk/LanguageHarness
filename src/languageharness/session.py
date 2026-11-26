"""Session state, budget/phase director, and the conversation engine.

The harness, not the model, decides when the conversation ends: the phase is derived
from the session's budget and state every turn, and the partner is told the phase in a
director note. After a `closing` reply the session is ended and input is locked.
"""
from __future__ import annotations

import asyncio
import math
import time
import uuid
from typing import AsyncIterator, Literal

from pydantic import BaseModel, Field

from . import corrector
from .config import ModelSlot, data_path, load_settings, read_json, write_json_atomic
from .prompting import render_dir
from .providers import ProviderError, get_provider

Phase = Literal["opening", "main", "wrap_up", "closing"]
Mode = Literal["turns", "time", "goal"]
WRAP_FRACTION = 0.2


class Target(BaseModel):
    expression: str
    reading: str = ""
    meaning: str = ""
    status: str = ""
    used: bool = False
    used_turn: int | None = None


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    ts: float = Field(default_factory=time.time)
    turn: int = 0
    phase: str | None = None
    correction: dict | None = None


class Session(BaseModel):
    id: str = Field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6])
    created: float = Field(default_factory=time.time)
    status: Literal["new", "ready", "active", "ended"] = "new"
    topic: str
    scenario: str = ""
    level: str = "intermediate"
    politeness: Literal["polite", "casual"] = "polite"
    mode: Mode = "turns"
    budget_turns: int = 15
    budget_minutes: float = 15
    goal: str = ""
    goal_max_turns: int = 30
    partner: ModelSlot
    utility: ModelSlot
    use_anki: bool = True
    use_research: bool = True
    brief: dict | None = None
    targets: list[Target] = Field(default_factory=list)
    anki_source: str | None = None
    anki_note: str | None = None
    messages: list[ChatMessage] = Field(default_factory=list)
    started_at: float | None = None
    ended_at: float | None = None
    checkpoints_met: list[str] = Field(default_factory=list)
    goal_met_turn: int | None = None
    force_close: bool = False
    review: dict | None = None

    # -- derived -------------------------------------------------------------
    @property
    def user_turns(self) -> int:
        return sum(1 for m in self.messages if m.role == "user")

    @property
    def checkpoints(self) -> list[str]:
        return list((self.brief or {}).get("goal_checkpoints") or [])


# --- persistence ----------------------------------------------------------------
def session_path(sid: str):
    if not sid.replace("-", "").isalnum():
        raise ValueError("bad session id")
    return data_path("sessions", f"{sid}.json")


def save(s: Session) -> None:
    write_json_atomic(session_path(s.id), s.model_dump())


def load(sid: str) -> Session | None:
    raw = read_json(session_path(sid))
    return Session.model_validate(raw) if raw else None


def list_sessions() -> list[dict]:
    out = []
    for p in sorted(data_path("sessions", "x").parent.glob("*.json"), reverse=True):
        raw = read_json(p) or {}
        out.append({k: raw.get(k) for k in ("id", "topic", "status", "mode", "created")} | {"turns": sum(1 for m in raw.get("messages", []) if m.get("role") == "user")})
    return out


# --- director ---------------------------------------------------------------------
def wrap_turns(n: int) -> int:
    return max(1, round(n * WRAP_FRACTION))


def compute_phase(s: Session, now: float | None = None) -> tuple[Phase, dict]:
    """Phase for the NEXT partner reply, plus info for the director note and UI."""
    now = now or time.time()
    k = s.user_turns
    info: dict = {"turn": k}
    if s.mode == "turns":
        n = s.budget_turns
        info |= {"budget": n, "remaining": max(0, n - k), "progress": min(1.0, k / max(n, 1))}
    elif s.mode == "time":
        elapsed = (now - s.started_at) / 60 if s.started_at else 0.0
        m = s.budget_minutes
        info |= {"budget_minutes": m, "elapsed_minutes": round(elapsed, 1), "remaining_minutes": round(max(0.0, m - elapsed), 1), "progress": min(1.0, elapsed / max(m, 0.1))}
    else:
        cps = s.checkpoints
        info |= {"checkpoints": len(cps), "met": len(s.checkpoints_met), "cap": s.goal_max_turns,
                 "progress": (len(s.checkpoints_met) / len(cps)) if cps else min(1.0, k / max(s.goal_max_turns, 1))}

    if s.force_close:
        return "closing", info
    if k == 0:
        return "opening", info
    if s.mode == "turns":
        if k >= s.budget_turns:
            return "closing", info
        if s.budget_turns - k <= wrap_turns(s.budget_turns):
            return "wrap_up", info
    elif s.mode == "time":
        if info["elapsed_minutes"] >= s.budget_minutes:
            return "closing", info
        if info["elapsed_minutes"] >= s.budget_minutes * (1 - WRAP_FRACTION):
            return "wrap_up", info
    else:
        cap = s.goal_max_turns
        if k >= cap:
            return "closing", info
        if s.goal_met_turn is not None:
            # met is detected after the reply for turn goal_met_turn: one wrap-up reply, then close
            return ("wrap_up" if k <= s.goal_met_turn + 1 else "closing"), info
        if cap - k <= wrap_turns(cap):
            return "wrap_up", info
    return "main", info


PHASE_HINT = {
    "opening": "Greet the learner and open the topic with an easy, inviting question.",
    "main": "Develop the conversation. Move along the arc; keep it engaging.",
    "wrap_up": "Begin wrapping up: no new subtopics. Tie threads together and steer toward a natural close.",
    "closing": "End the conversation NOW in this reply: briefly sum up, thank them, say goodbye. Do not ask any question.",
}


def director_note(s: Session, phase: Phase, info: dict) -> str:
    lines = [f"[進行メモ — hidden from the learner] phase: {phase}. {PHASE_HINT[phase]}"]
    if s.mode == "turns":
        lines.append(f"Exchanges left after this reply: {info['remaining']} of {info['budget']}.")
    elif s.mode == "time":
        lines.append(f"Time left: about {info['remaining_minutes']} of {info['budget_minutes']} minutes.")
    else:
        lines.append(f"Goal: {s.goal}")
        todo = [c for c in s.checkpoints if c not in s.checkpoints_met]
        if todo and phase in ("opening", "main"):
            lines.append("Goal checkpoints not yet done (create natural chances for the learner to do them): " + "; ".join(todo))
    arc = (s.brief or {}).get("arc") or []
    if arc and phase in ("opening", "main"):
        idx = min(len(arc) - 1, math.floor(info.get("progress", 0) * len(arc)))
        lines.append(f"Arc step now: {arc[idx]}")
    unused = [t.expression for t in s.targets if not t.used]
    if unused and phase in ("opening", "main", "wrap_up"):
        lines.append("Target words the learner hasn't used yet (invite 1–2 naturally): " + "、".join(unused[:8]))
    return "\n".join(lines)


def system_prompt(s: Session) -> str:
    politeness = "です・ます調（丁寧語）" if s.politeness == "polite" else "タメ口（カジュアル）"
    parts = [render_dir("partner", topic=s.topic, scenario=s.scenario or "特になし（友人同士の雑談）", level=s.level, politeness=politeness)]
    b = s.brief or {}
    if b:
        mat = ["# 話題の資料", b.get("summary", "")]
        mat += [f"- {f}" for f in b.get("facts", [])]
        if b.get("vocab"):
            mat.append("話題の語彙: " + "、".join(f"{v['word']}（{v.get('reading','')}）" for v in b["vocab"]))
        if b.get("arc"):
            mat.append("会話の流れ（アーク）: " + " → ".join(b["arc"]))
        parts.append("\n".join(mat))
    if s.mode == "goal" and s.goal:
        parts.append(f"# 会話のゴール\n{s.goal}\n学習者がゴールを達成できるよう、自然に機会を作ってください。")
    if s.targets:
        parts.append("# ターゲット語彙（学習者が既に知っている語）\n" + "\n".join(f"- {t.expression}（{t.reading}）{t.meaning}" for t in s.targets))
    return "\n\n".join(parts)


def build_messages(s: Session, phase: Phase, info: dict) -> list[dict]:
    msgs = [{"role": "system", "content": system_prompt(s)}]
    msgs += [{"role": m.role, "content": m.content} for m in s.messages]
    if not s.messages:
        msgs.append({"role": "user", "content": "(The learner has joined. Start the conversation now, in Japanese.)"})
    msgs.append({"role": "system", "content": director_note(s, phase, info)})
    return msgs


def public_state(s: Session) -> dict:
    phase, info = compute_phase(s) if s.status != "ended" else ("closing", {})
    return {"status": s.status, "phase": phase, "info": info, "targets": [t.model_dump() for t in s.targets],
            "checkpoints": s.checkpoints, "checkpoints_met": s.checkpoints_met, "started_at": s.started_at,
            "mode": s.mode, "budget_turns": s.budget_turns, "budget_minutes": s.budget_minutes, "turn": s.user_turns}


# --- engine -------------------------------------------------------------------------
_locks: dict[str, asyncio.Lock] = {}


def lock_for(sid: str) -> asyncio.Lock:
    return _locks.setdefault(sid, asyncio.Lock())


async def _partner_reply(s: Session) -> AsyncIterator[dict]:
    phase, info = compute_phase(s)
    provider = get_provider(s.partner.provider)
    text = ""
    async for piece in provider.stream_chat(s.partner.model, build_messages(s, phase, info)):
        text += piece
        yield {"event": "delta", "data": {"text": piece}}
    text = text.strip()
    s.messages.append(ChatMessage(role="assistant", content=text, turn=s.user_turns, phase=phase))
    if phase == "closing":
        s.status, s.ended_at = "ended", time.time()
    yield {"event": "assistant_done", "data": {"text": text, "phase": phase}}


async def open_conversation(s: Session) -> AsyncIterator[dict]:
    if s.messages:
        raise ProviderError("Conversation already started.")
    s.status, s.started_at = "active", time.time()
    async for ev in _partner_reply(s):
        yield ev
    save(s)
    yield {"event": "state", "data": public_state(s)}


def mark_used(s: Session, names: list[str], text: str) -> list[str]:
    newly = []
    for t in s.targets:
        if t.used:
            continue
        if t.expression in names or (len(t.expression) >= 2 and t.expression in text) or (len(t.reading) >= 3 and t.reading in text):
            t.used, t.used_turn = True, s.user_turns
            newly.append(t.expression)
    return newly


async def user_message(s: Session, text: str) -> AsyncIterator[dict]:
    if s.status != "active":
        raise ProviderError("This conversation has ended.")
    text = text.strip()
    if not text:
        raise ProviderError("Empty message.")
    context = s.messages[-6:]
    s.messages.append(ChatMessage(role="user", content=text, turn=s.user_turns + 1))
    user_idx = len(s.messages) - 1
    mark_used(s, [], text)  # cheap pass so this reply's director note is current
    corr_task = asyncio.create_task(corrector.correct(s, text, context))
    try:
        async for ev in _partner_reply(s):
            yield ev
    finally:
        try:
            corr = await corr_task
        except Exception as e:  # noqa: BLE001 - corrections are best-effort
            corr = {"error": str(e)}
    s.messages[user_idx].correction = corr
    if not corr.get("error"):
        mark_used(s, corr.get("target_words_used") or [], "")
        for cp in corr.get("goal_checkpoints_met") or []:
            if cp in s.checkpoints and cp not in s.checkpoints_met:
                s.checkpoints_met.append(cp)
        if s.mode == "goal" and s.checkpoints and s.goal_met_turn is None and set(s.checkpoints) <= set(s.checkpoints_met):
            s.goal_met_turn = s.user_turns
    yield {"event": "correction", "data": {"index": user_idx, "correction": corr}}
    save(s)
    yield {"event": "state", "data": public_state(s)}


async def close_now(s: Session) -> AsyncIterator[dict]:
    if s.status != "active":
        raise ProviderError("This conversation is not active.")
    s.force_close = True
    async for ev in _partner_reply(s):
        yield ev
    save(s)
    yield {"event": "state", "data": public_state(s)}


async def prepare(s: Session) -> AsyncIterator[dict]:
    """Research the topic and choose Anki targets. Leaves the session 'ready'."""
    from .anki import AnkiUnavailable, load_known_items
    from .anki.select import pick_targets
    from .research import build_brief

    settings = load_settings()
    util = get_provider(s.utility.provider)
    queue: asyncio.Queue = asyncio.Queue()

    async def progress(msg: str):
        await queue.put({"event": "progress", "data": {"message": msg}})

    async def do_brief():
        await progress("話題をリサーチしています…" if s.use_research else "資料を準備しています…")
        try:
            s.brief = await build_brief(util, s.utility.model, topic=s.topic, scenario=s.scenario, level=s.level, mode=s.mode,
                                        goal=s.goal, search=s.use_research and settings.research_enabled,
                                        max_results=settings.tavily_max_results, progress=progress)
        except ProviderError as e:
            s.brief = {"summary": "", "facts": [], "vocab": [], "arc": [], "sources": [],
                       "goal_checkpoints": [s.goal] if s.mode == "goal" and s.goal else []}
            await progress(f"資料の作成に失敗しました（モデルの知識だけで続けます）: {e}"[:300])

    async def do_anki():
        if not s.use_anki:
            return
        await progress("Ankiのカードを読み込んでいます…")
        try:
            kind, items = await load_known_items(settings.anki)
        except AnkiUnavailable as e:
            s.anki_note = f"Anki unavailable: {e}"
            await progress(s.anki_note)
            return
        s.anki_source = kind
        await progress(f"Anki ({kind}): 復習済みカード {len(items)} 枚。ターゲット語彙を選んでいます…")
        picked = await pick_targets(util, s.utility.model, items, topic=s.topic, scenario=s.scenario, level=s.level,
                                    count=settings.anki.target_count, candidate_limit=settings.anki.candidate_limit)
        s.targets = [Target(expression=i.expression, reading=i.reading, meaning=i.meaning, status=i.status) for i in picked]
        if not items:
            s.anki_note = "No reviewed cards found in the selected deck(s)."

    async def run():
        try:
            await asyncio.gather(do_brief(), do_anki())
            await queue.put(None)
        except Exception as e:  # noqa: BLE001
            await queue.put(e)

    task = asyncio.create_task(run())
    while True:
        item = await queue.get()
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        yield item
    await task
    s.status = "ready"
    save(s)
    yield {"event": "ready", "data": {"brief": s.brief, "targets": [t.model_dump() for t in s.targets], "anki_note": s.anki_note, "anki_source": s.anki_source}}
