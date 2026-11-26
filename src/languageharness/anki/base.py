"""Shared Anki types and field normalization."""
from __future__ import annotations

import html
import re

from pydantic import BaseModel

MATURE_DAYS = 21


class KnownItem(BaseModel):
    card_id: int
    note_id: int = 0
    note_type: str = ""
    deck: str = ""
    expression: str
    reading: str = ""
    meaning: str = ""
    interval: int = 0  # days
    last_review: float | None = None  # epoch seconds
    lapses: int = 0

    @property
    def status(self) -> str:
        return "mature" if self.interval >= MATURE_DAYS else "young"


_TAG = re.compile(r"<[^>]+>")
_SOUND = re.compile(r"\[sound:[^\]]*\]")
_KANJI = r"[㐀-䶿一-鿿豈-﫿々〆ヶ]"
_FURI = re.compile(rf" ?({_KANJI}+)\[([^\]]+)\]")
_BR = re.compile(r"<br\s*/?>|</div>|</p>", re.I)


def normalize_field(value: str) -> str:
    """Strip HTML, sound tags and entities; collapse whitespace."""
    value = _BR.sub(" ; ", value or "")
    value = _SOUND.sub("", value)
    value = _TAG.sub("", value)
    value = html.unescape(value).replace("\xa0", " ")
    value = re.sub(r"\s*;\s*(;\s*)*", "; ", value).strip(" ;")
    return re.sub(r"\s+", " ", value).strip()


def split_furigana(value: str) -> tuple[str, str]:
    """'日本語[にほんご]を 勉強[べんきょう]' -> ('日本語を勉強', 'にほんごをべんきょう'). No brackets -> (value, '')."""
    if not _FURI.search(value):
        return value, ""
    surface = _FURI.sub(r"\1", value).replace(" ", "")
    reading = _FURI.sub(r"\2", value).replace(" ", "")
    return surface, reading


# Order matters: vocab-ish names beat "Expression", which in many decks (Core 2k/6k, subs2srs) holds the sentence.
_EXPR = ["vocabulary-kanji", "vocab-kanji", "vocabulary", "vocab", "word", "target", "expression", "kanji", "japanese", "term", "front", "key"]
_READ = ["furigana", "reading", "kana", "hiragana", "yomi", "pronunciation"]
_MEAN = ["meaning", "english", "definition", "glossary", "gloss", "translation", "back"]
_NOT_EXPR = ("audio", "sentence", "example", "english", "meaning", "definition", "furigana", "reading", "kana", "image", "pos")


def _first_token(name: str) -> str:
    return re.split(r"[-_ ]|(?<=[a-z])(?=[A-Z])", name)[0].lower()


def _pick(names: list[str], wanted: list[str], exclude: set[str], avoid: tuple[str, ...] = ("audio", "sentence", "example")) -> str:
    low = [(n, n.lower()) for n in names if n not in exclude]
    for w in wanted:  # exact first
        for n, l in low:
            if l == w:
                return n
    for w in wanted:  # then contains
        for n, l in low:
            if w in l and not any(a in l for a in avoid):
                return n
    return ""


def _pick_related(names: list[str], expr: str, wanted: list[str], exclude: set[str]) -> str:
    """Prefer fields sharing the expression field's prefix (Vocabulary-Kanji -> Vocabulary-Furigana)."""
    tok = _first_token(expr) if expr else ""
    if tok:
        same = [n for n in names if n != expr and n.lower().startswith(tok) and n.lower() != tok]
        hit = _pick(same, wanted, exclude)
        if hit:
            return hit
    return _pick(names, wanted, exclude)


def guess_field_map(field_names: list[str]) -> dict[str, str]:
    expr = _pick(field_names, _EXPR, set(), avoid=_NOT_EXPR) or (field_names[0] if field_names else "")
    read = _pick_related(field_names, expr, _READ, {expr})
    mean = _pick_related(field_names, expr, _MEAN, {expr, read}) or next((n for n in field_names if n not in {expr, read}), "")
    return {"expression": expr, "reading": read, "meaning": mean}


def build_item(*, card_id: int, note_id: int, note_type: str, deck: str, fields: dict[str, str], fmap: dict[str, str],
               interval: int, last_review: float | None, lapses: int) -> KnownItem | None:
    raw_expr = normalize_field(fields.get(fmap.get("expression", ""), ""))
    if not raw_expr:
        return None
    expr, furi = split_furigana(raw_expr)
    reading_raw = normalize_field(fields.get(fmap.get("reading", ""), "")) if fmap.get("reading") else ""
    reading = split_furigana(reading_raw)[1] or reading_raw or furi
    meaning = normalize_field(fields.get(fmap.get("meaning", ""), "")) if fmap.get("meaning") else ""
    return KnownItem(card_id=card_id, note_id=note_id, note_type=note_type, deck=deck, expression=expr[:80],
                     reading=reading[:80], meaning=meaning[:160], interval=max(interval, 0), last_review=last_review, lapses=lapses)


def dedupe(items: list[KnownItem]) -> list[KnownItem]:
    """One entry per expression; keep the strongest card (max interval), latest review."""
    best: dict[str, KnownItem] = {}
    for it in items:
        cur = best.get(it.expression)
        if cur is None:
            best[it.expression] = it
            continue
        merged = cur if cur.interval >= it.interval else it
        lr = max(filter(None, [cur.last_review, it.last_review]), default=None)
        best[it.expression] = merged.model_copy(update={"last_review": lr, "lapses": max(cur.lapses, it.lapses)})
    return list(best.values())
