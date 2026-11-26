"""End-of-session review."""
from __future__ import annotations

import csv
import io

from .prompting import render
from .providers import get_provider
from .session import Session, save

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "recurring_mistakes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "examples": {"type": "array", "items": {"type": "string"}}, "tip": {"type": "string"}},
                "required": ["pattern", "examples", "tip"],
            },
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "new_words": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"word": {"type": "string"}, "reading": {"type": "string"}, "meaning": {"type": "string"}, "example": {"type": "string"}},
                "required": ["word", "reading", "meaning", "example"],
            },
        },
        "focus_next": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "recurring_mistakes", "strengths", "new_words", "focus_next"],
}


async def make_review(s: Session) -> dict:
    who = {"user": "Learner", "assistant": "Partner"}
    corrections = []
    for m in s.messages:
        c = m.correction or {}
        if m.role == "user" and c and not c.get("ok", True):
            notes = "; ".join(f"{n['original']} → {n['fix']} ({n['explanation']})" for n in c.get("notes", []))
            corrections.append(f"- \"{m.content}\" → \"{c.get('corrected','')}\" {notes}")
    prompt = render(
        "review.md",
        level=s.level,
        topic=s.topic,
        targets="\n".join(f"- {t.expression} ({t.reading}) {t.meaning}: {'USED' if t.used else 'not used'}" for t in s.targets) or "(none)",
        corrections="\n".join(corrections) or "(no corrections)",
        transcript="\n".join(f"{who[m.role]}: {m.content}" for m in s.messages),
    )
    out = await get_provider(s.utility.provider).complete_json(s.utility.model, [{"role": "user", "content": prompt}], SCHEMA)
    known = {t.expression for t in s.targets}
    out["new_words"] = [w for w in out.get("new_words") or [] if isinstance(w, dict) and w.get("word") not in known]
    out["targets_used"] = [t.expression for t in s.targets if t.used]
    out["targets_missed"] = [t.expression for t in s.targets if not t.used]
    out["stats"] = {
        "turns": s.user_turns,
        "minutes": round(((s.ended_at or s.started_at or 0) - (s.started_at or 0)) / 60, 1),
        "messages_ok": sum(1 for m in s.messages if m.role == "user" and (m.correction or {}).get("ok")),
    }
    s.review = out
    save(s)
    return out


def new_words_tsv(words: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    for x in words:
        w.writerow([x.get("word", ""), x.get("reading", ""), x.get("meaning", ""), x.get("example", "")])
    return buf.getvalue()
