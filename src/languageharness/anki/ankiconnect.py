"""AnkiConnect client (Anki desktop + AnkiConnect add-on, default 127.0.0.1:8765)."""
from __future__ import annotations

import httpx

from .base import KnownItem, build_item, dedupe, guess_field_map

BATCH = 500


class AnkiConnectError(RuntimeError):
    pass


class AnkiConnect:
    def __init__(self, url: str = "http://127.0.0.1:8765", transport: httpx.AsyncBaseTransport | None = None):
        self.url = url
        self._transport = transport

    async def invoke(self, action: str, **params):
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as c:
                r = await c.post(self.url, json={"action": action, "version": 6, "params": params})
        except httpx.HTTPError as e:
            raise AnkiConnectError(f"AnkiConnect not reachable at {self.url}") from e
        body = r.json()
        if body.get("error"):
            raise AnkiConnectError(f"AnkiConnect {action}: {body['error']}")
        return body.get("result")

    async def available(self) -> bool:
        try:
            return bool(await self.invoke("version"))
        except AnkiConnectError:
            return False

    async def deck_names(self) -> list[str]:
        return sorted(await self.invoke("deckNames"))

    async def note_types(self) -> dict[str, list[str]]:
        names = await self.invoke("modelNames")
        return {n: await self.invoke("modelFieldNames", modelName=n) for n in names}

    async def known_items(self, decks: list[str], field_maps: dict[str, dict]) -> list[KnownItem]:
        deck_q = " OR ".join(f'"deck:{d}"' for d in decks) if decks else "deck:*"
        ids: list[int] = await self.invoke("findCards", query=f"({deck_q}) -is:new -is:suspended")
        items: list[KnownItem] = []
        for i in range(0, len(ids), BATCH):
            chunk = ids[i : i + BATCH]
            infos = await self.invoke("cardsInfo", cards=chunk)
            reviews = await self.invoke("getReviewsOfCards", cards=[str(c) for c in chunk]) or {}
            for info in infos:
                fields = {k: v["value"] for k, v in (info.get("fields") or {}).items()}
                ordered = [k for k, _ in sorted((info.get("fields") or {}).items(), key=lambda kv: kv[1].get("order", 0))]
                fmap = field_maps.get(info.get("modelName", "")) or guess_field_map(ordered)
                revs = reviews.get(str(info["cardId"])) or []
                last = max((rv["id"] for rv in revs), default=None)
                it = build_item(
                    card_id=info["cardId"], note_id=info.get("note", 0), note_type=info.get("modelName", ""),
                    deck=info.get("deckName", ""), fields=fields, fmap=fmap, interval=info.get("interval", 0),
                    last_review=last / 1000 if last else None, lapses=info.get("lapses", 0),
                )
                if it:
                    items.append(it)
        return dedupe(items)

    async def add_note(self, deck: str, model: str, fields: dict[str, str], tags: list[str] | None = None) -> int:
        return await self.invoke(
            "addNote",
            note={"deckName": deck, "modelName": model, "fields": fields, "tags": tags or ["languageharness"],
                  "options": {"allowDuplicate": False, "duplicateScope": "deck"}},
        )
