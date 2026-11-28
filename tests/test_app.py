from fastapi.testclient import TestClient

from languageharness import config
from languageharness.app import app


def events(resp):
    out = []
    for chunk in resp.text.split("\n\n"):
        if chunk.startswith("event: "):
            out.append(chunk.split("\n")[0][7:])
    return out


def test_api_flow(fake):
    config.save_settings(config.Settings(partner=config.ModelSlot(model="p"), utility=config.ModelSlot(model="u")))
    c = TestClient(app)
    assert c.get("/").status_code == 200
    s = c.post("/api/sessions", json={"topic": "旅行", "budget_turns": 2, "use_anki": False}).json()
    sid = s["id"]
    assert "ready" in events(c.post(f"/api/sessions/{sid}/prepare"))
    assert events(c.post(f"/api/sessions/{sid}/start"))[-1] == "done"
    for _ in range(2):
        ev = events(c.post(f"/api/sessions/{sid}/message", json={"text": "楽しかったです"}))
        assert "correction" in ev and ev[-1] == "done"
    st = c.get(f"/api/sessions/{sid}").json()["state"]
    assert st["status"] == "ended"
    assert "error" in events(c.post(f"/api/sessions/{sid}/message", json={"text": "もう一回"}))
    assert c.post(f"/api/sessions/{sid}/review").json()["summary"] == "Good."
    assert "替え玉" in c.get(f"/api/sessions/{sid}/new-words.tsv").text
    assert c.get("/api/sessions").json()["sessions"][0]["id"] == sid


def test_create_requires_models(data_dir):
    c = TestClient(app)
    assert c.post("/api/sessions", json={"topic": "x"}).status_code == 400


def test_secrets_are_private(data_dir):
    import os

    c = TestClient(app)
    assert c.put("/api/secrets", json={"name": "TAVILY_API_KEY", "value": "tvly-x"}).json()["TAVILY_API_KEY"] is True
    assert oct(os.stat(config.auth_path()).st_mode & 0o777) == "0o600"
    assert c.put("/api/secrets", json={"name": "EVIL", "value": "x"}).status_code == 400


def test_pwa_assets(data_dir):
    c = TestClient(app)
    sw = c.get("/sw.js")
    assert sw.status_code == 200 and "javascript" in sw.headers["content-type"]
    assert sw.headers["cache-control"] == "no-cache" and sw.headers["service-worker-allowed"] == "/"
    m = c.get("/manifest.webmanifest")
    assert m.headers["content-type"].startswith("application/manifest+json")
    assert m.json()["display"] == "standalone"
    for icon in m.json()["icons"]:
        assert c.get(icon["src"]).status_code == 200
    page = c.get("/")
    assert page.headers["cache-control"] == "no-cache" and 'rel="manifest"' in page.text
    assert c.get("/static/icons/apple-touch-icon.png").status_code == 200


def test_turn_completes_after_client_disconnect(fake):
    import time

    from languageharness import session as sess

    config.save_settings(config.Settings(partner=config.ModelSlot(model="p"), utility=config.ModelSlot(model="u")))
    with TestClient(app) as c:  # one event loop for the whole test, like a real server
        sid = c.post("/api/sessions", json={"topic": "旅行", "budget_turns": 5, "use_anki": False}).json()["id"]
        c.post(f"/api/sessions/{sid}/prepare")
        c.post(f"/api/sessions/{sid}/start")

        gate = {"open": False}
        original = fake.stream_chat

        async def slow_stream(model, messages):
            import asyncio

            yield "前半"
            while not gate["open"]:  # hold the reply until the client has gone away
                await asyncio.sleep(0.01)
            async for piece in original(model, messages):
                yield piece

        fake.stream_chat = slow_stream
        fake.replies = ["いいですね。"]
        with c.stream("POST", f"/api/sessions/{sid}/message", json={"text": "京都に行きました"}) as r:
            for line in r.iter_lines():
                if line.startswith("event: delta"):
                    break  # client disconnects after the first delta
        assert c.get(f"/api/sessions/{sid}").json()["busy"] is True
        gate["open"] = True
        for _ in range(200):
            if not c.get(f"/api/sessions/{sid}").json()["busy"]:
                break
            time.sleep(0.01)
        s = sess.load(sid)
        assert [m.role for m in s.messages] == ["assistant", "user", "assistant"]
        assert s.messages[-1].content == "前半いいですね。" and s.messages[1].correction is not None
