"""FastAPI app: JSON API + SSE streams + static UI."""
from __future__ import annotations

import json
import os
import threading
import webbrowser
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, session as sess
from .anki import AnkiUnavailable, guess_field_map, list_decks, list_note_types
from .anki.ankiconnect import AnkiConnect, AnkiConnectError
from .anki.sqlite_reader import candidate_collections
from .providers import ProviderError, get_provider
from .providers import chatgpt_oauth

app = FastAPI(title="LanguageHarness")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


# --- settings & secrets -----------------------------------------------------------
@app.get("/api/settings")
def get_settings():
    return {"settings": config.load_settings().model_dump(), "secrets": config.secret_status(), "chatgpt": chatgpt_oauth.status()}


@app.put("/api/settings")
def put_settings(body: dict):
    s = config.Settings.model_validate(body)
    config.save_settings(s)
    return s.model_dump()


class SecretIn(BaseModel):
    name: str
    value: str


@app.put("/api/secrets")
def put_secret(body: SecretIn):
    try:
        config.set_secret(body.name, body.value.strip())
    except ValueError:
        raise HTTPException(400, "unknown secret")
    return config.secret_status()


@app.get("/api/models")
async def models(provider: str):
    try:
        return {"models": await get_provider(provider).list_models()}
    except ProviderError as e:
        return {"models": [], "error": str(e)}


# --- ChatGPT login ------------------------------------------------------------------
@app.post("/api/chatgpt/login")
def chatgpt_login():
    try:
        return {"url": chatgpt_oauth.start_login(open_browser=True)}
    except ProviderError as e:
        raise HTTPException(409, str(e))


@app.get("/api/chatgpt/status")
def chatgpt_status():
    return chatgpt_oauth.status()


@app.post("/api/chatgpt/logout")
def chatgpt_logout():
    chatgpt_oauth.logout()
    return chatgpt_oauth.status()


# --- Anki ---------------------------------------------------------------------------
@app.get("/api/anki/decks")
async def anki_decks():
    try:
        kind, decks = await list_decks(config.load_settings().anki)
        return {"source": kind, "decks": decks}
    except AnkiUnavailable as e:
        return {"source": None, "decks": [], "error": str(e)}


@app.get("/api/anki/note-types")
async def anki_note_types():
    s = config.load_settings().anki
    try:
        kind, types = await list_note_types(s)
    except AnkiUnavailable as e:
        return {"source": None, "types": [], "error": str(e)}
    out = []
    for name, fields in sorted(types.items()):
        saved = s.field_maps.get(name)
        out.append({"name": name, "fields": fields, "map": saved.model_dump() if saved and saved.expression else guess_field_map(fields), "saved": bool(saved and saved.expression)})
    return {"source": kind, "types": out}


@app.get("/api/anki/collections")
def anki_collections():
    return {"paths": [str(p) for p in candidate_collections()]}


# --- sessions -----------------------------------------------------------------------
class SessionIn(BaseModel):
    topic: str
    scenario: str = ""
    level: str = "intermediate"
    politeness: str = "polite"
    mode: str = "turns"
    budget_turns: int = 15
    budget_minutes: float = 15
    goal: str = ""
    goal_max_turns: int = 30
    use_anki: bool = True
    use_research: bool = True


def _load(sid: str) -> sess.Session:
    try:
        s = sess.load(sid)
    except ValueError:
        s = None
    if not s:
        raise HTTPException(404, "session not found")
    return s


@app.get("/api/sessions")
def sessions():
    return {"sessions": sess.list_sessions()}


@app.post("/api/sessions")
def create_session(body: SessionIn):
    st = config.load_settings()
    if not body.topic.strip():
        raise HTTPException(400, "topic required")
    for slot in (st.partner, st.utility):
        if not slot.model:
            raise HTTPException(400, "Choose partner and utility models in Settings first.")
    s = sess.Session(**body.model_dump(), partner=st.partner, utility=st.utility)
    s.budget_turns = max(2, s.budget_turns)
    s.budget_minutes = max(1, s.budget_minutes)
    s.goal_max_turns = max(4, s.goal_max_turns)
    sess.save(s)
    return s.model_dump()


@app.get("/api/sessions/{sid}")
def get_session(sid: str):
    s = _load(sid)
    return {"session": s.model_dump(), "state": sess.public_state(s)}


@app.delete("/api/sessions/{sid}")
def delete_session(sid: str):
    _load(sid)
    sess.session_path(sid).unlink(missing_ok=True)
    return {"ok": True}


def _sse(sid: str, make: callable) -> StreamingResponse:
    """Run a session generator under the session lock; state is only saved by the generator on success."""

    async def gen() -> AsyncIterator[bytes]:
        lock = sess.lock_for(sid)
        if lock.locked():
            yield _ev("error", {"message": "Busy: a reply is already being generated."})
            return
        async with lock:
            s = _load(sid)  # fresh copy; failures leave the saved session untouched
            try:
                async for ev in make(s):
                    yield _ev(ev["event"], ev["data"])
                yield _ev("done", {})
            except Exception as e:  # noqa: BLE001
                yield _ev("error", {"message": str(e)})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _ev(event: str, data) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


@app.post("/api/sessions/{sid}/prepare")
def prepare(sid: str):
    return _sse(sid, sess.prepare)


@app.post("/api/sessions/{sid}/start")
def start(sid: str):
    return _sse(sid, sess.open_conversation)


class MessageIn(BaseModel):
    text: str


@app.post("/api/sessions/{sid}/message")
def message(sid: str, body: MessageIn):
    return _sse(sid, lambda s: sess.user_message(s, body.text))


@app.post("/api/sessions/{sid}/close")
def close(sid: str):
    return _sse(sid, sess.close_now)


@app.post("/api/sessions/{sid}/review")
async def review(sid: str):
    from .review import make_review

    s = _load(sid)
    if s.status != "ended":
        raise HTTPException(409, "Conversation has not ended yet.")
    try:
        return await make_review(s)
    except ProviderError as e:
        raise HTTPException(502, str(e))


@app.get("/api/sessions/{sid}/new-words.tsv")
def new_words(sid: str):
    from .review import new_words_tsv

    s = _load(sid)
    return PlainTextResponse(new_words_tsv((s.review or {}).get("new_words", [])), media_type="text/tab-separated-values",
                             headers={"Content-Disposition": f'attachment; filename="{sid}-new-words.tsv"'})


class AnkiAddIn(BaseModel):
    deck: str
    model: str = "Basic"
    front_field: str = "Front"
    back_field: str = "Back"
    words: list[dict]


@app.post("/api/anki/add")
async def anki_add(body: AnkiAddIn):
    ac = AnkiConnect(config.load_settings().anki.ankiconnect_url)
    if not await ac.available():
        raise HTTPException(409, "AnkiConnect is not reachable (open Anki with the AnkiConnect add-on).")
    added, errors = 0, []
    for w in body.words:
        back = "<br>".join(x for x in (w.get("reading", ""), w.get("meaning", ""), w.get("example", "")) if x)
        try:
            await ac.add_note(body.deck, body.model, {body.front_field: w.get("word", ""), body.back_field: back})
            added += 1
        except AnkiConnectError as e:
            errors.append(f"{w.get('word')}: {e}")
    return {"added": added, "errors": errors}


def main():
    import uvicorn

    host = os.environ.get("LH_HOST", "127.0.0.1")
    port = int(os.environ.get("LH_PORT", "8000"))
    if os.environ.get("LH_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
