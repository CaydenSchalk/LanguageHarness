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
