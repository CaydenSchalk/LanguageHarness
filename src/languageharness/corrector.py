"""Per-message correction + target-word / goal detection, on the utility model."""
from __future__ import annotations

from typing import TYPE_CHECKING

from .prompting import render
from .providers import get_provider

if TYPE_CHECKING:
    from .session import ChatMessage, Session

SCHEMA = {
    "type": "object",
    "properties": {
        "ok": {"type": "boolean"},
        "corrected": {"type": "string"},
        "notes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"original": {"type": "string"}, "fix": {"type": "string"}, "explanation": {"type": "string"}},
                "required": ["original", "fix", "explanation"],
            },
        },
        "target_words_used": {"type": "array", "items": {"type": "string"}},
        "goal_checkpoints_met": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["ok", "corrected", "notes", "target_words_used", "goal_checkpoints_met"],
}


async def correct(s: "Session", text: str, context: list["ChatMessage"]) -> dict:
    who = {"user": "Learner", "assistant": "Partner"}
    prompt = render(
        "corrector.md",
        level=s.level,
        politeness="polite です/ます" if s.politeness == "polite" else "casual",
        targets="\n".join(f"- {t.expression} ({t.reading})" for t in s.targets) or "(none)",
        checkpoints="\n".join(f"- {c}" for c in s.checkpoints if c not in s.checkpoints_met) or "(none)",
        context="\n".join(f"{who[m.role]}: {m.content}" for m in context) or "(start of conversation)",
        message=text,
    )
    out = await get_provider(s.utility.provider).complete_json(s.utility.model, [{"role": "user", "content": prompt}], SCHEMA)
    out.setdefault("ok", True)
    out.setdefault("corrected", text)
    out["notes"] = [n for n in out.get("notes") or [] if isinstance(n, dict) and n.get("original", "").strip() != n.get("fix", "").strip()][:4]
    out.setdefault("target_words_used", [])
    out.setdefault("goal_checkpoints_met", [])
    if out["ok"] and not out["notes"]:
        out["corrected"] = text
    return out
