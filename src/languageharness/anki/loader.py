"""Pick AnkiConnect or the SQLite fallback according to settings."""
from __future__ import annotations

import asyncio

from ..config import AnkiSettings
from .ankiconnect import AnkiConnect, AnkiConnectError
from .base import KnownItem
from .sqlite_reader import SqliteAnki


class AnkiUnavailable(RuntimeError):
    pass


async def _source(s: AnkiSettings):
    """Returns ('ankiconnect', client) or ('sqlite', reader)."""
    errors = []
    if s.source in ("auto", "ankiconnect"):
        ac = AnkiConnect(s.ankiconnect_url)
        if await ac.available():
            return "ankiconnect", ac
        errors.append(f"AnkiConnect not reachable at {s.ankiconnect_url}")
    if s.source in ("auto", "sqlite"):
        try:
            return "sqlite", SqliteAnki(s.collection_path or None)
        except FileNotFoundError as e:
            errors.append(str(e))
    raise AnkiUnavailable("; ".join(errors))


def _maps(s: AnkiSettings) -> dict[str, dict]:
    return {k: v.model_dump() for k, v in s.field_maps.items() if v.expression}


async def load_known_items(s: AnkiSettings) -> tuple[str, list[KnownItem]]:
    kind, src = await _source(s)
    if kind == "ankiconnect":
        try:
            return kind, await src.known_items(s.decks, _maps(s))
        except AnkiConnectError as e:
            raise AnkiUnavailable(str(e)) from e
    return kind, await asyncio.to_thread(src.known_items, s.decks, _maps(s))


async def list_decks(s: AnkiSettings) -> tuple[str, list[str]]:
    kind, src = await _source(s)
    return kind, (await src.deck_names() if kind == "ankiconnect" else await asyncio.to_thread(src.deck_names))


async def list_note_types(s: AnkiSettings) -> tuple[str, dict[str, list[str]]]:
    kind, src = await _source(s)
    return kind, (await src.note_types() if kind == "ankiconnect" else await asyncio.to_thread(src.note_types))
