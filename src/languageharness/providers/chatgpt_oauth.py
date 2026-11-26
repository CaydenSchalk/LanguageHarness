"""ChatGPT subscription login (PKCE public-client OAuth).

Python port of the flow in learnharness/electron/chatgpt.cjs:
- authorize at auth.openai.com with client_id=dynamic_agent_client (a client id is
  issued during consent and returned on the callback),
- one-shot loopback listener on 127.0.0.1:<random>/auth/callback,
- code exchange with resource=https://api.openai.com/v1,
- id_token verified against the issuer JWKS,
- refresh via grant_type=refresh_token,
- tokens stored in data/chatgpt-credentials.json (atomic, chmod 600).

Requests then go to the normal OpenAI Responses API with the access token as the key.
This depends on OpenAI's endpoints accepting this flow; the API-key provider is independent.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import AsyncIterator
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import jwt

from ..config import data_path, read_json, write_json_atomic
from .base import Message, ProviderError
from .openai_api import OpenAIProvider

ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZE = f"{ISSUER}/api/accounts/authorize"
TOKEN = f"{ISSUER}/api/accounts/oauth/token"
JWKS = f"{ISSUER}/.well-known/jwks.json"
SCOPES = ["openid", "profile", "email", "offline_access", "resource.invoke", "chatgpt.tokens.use.direct"]
LOGIN_TIMEOUT = 300


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _cred_path():
    return data_path("chatgpt-credentials.json")


def _load() -> dict:
    state = read_json(_cred_path(), None) or {}
    if not str(state.get("hostId", "")).startswith("urn:uuid:"):  # OpenAI requires a URN-form host id
        state["hostId"] = f"urn:uuid:{uuid.uuid4()}"
        _save(state)
    return state


def _save(state: dict) -> None:
    write_json_atomic(_cred_path(), state, private=True)


def _grant(fields: dict) -> dict:
    r = httpx.post(
        TOKEN,
        data={**fields, "resource": RESOURCE},
        headers={"accept": "application/json"},
        timeout=15,
        follow_redirects=False,
    )
    if r.status_code != 200:
        try:
            err = r.json().get("error")
        except ValueError:
            err = None
        raise ProviderError(f"ChatGPT token request failed ({r.status_code}{', ' + str(err) if err else ''}).")
    return r.json()


def _verify_id_token(id_token: str, client_id: str, nonce: str | None) -> dict:
    keys = httpx.get(JWKS, timeout=15).json()
    jwks = jwt.PyJWKSet.from_dict(keys)
    kid = jwt.get_unverified_header(id_token).get("kid")
    key = next((k for k in jwks.keys if k.key_id == kid), None)
    if key is None:
        raise ProviderError("ChatGPT sign-in could not be verified (unknown signing key).")
    payload = jwt.decode(
        id_token, key.key, algorithms=["RS256"], audience=client_id, issuer=ISSUER, leeway=5,
        options={"require": ["sub", "exp", "iat"]},
    )
    if nonce is not None and payload.get("nonce") != nonce:
        raise ProviderError("ChatGPT sign-in could not be verified (nonce mismatch).")
    if payload.get("azp") not in (None, client_id):
        raise ProviderError("ChatGPT sign-in could not be verified (azp mismatch).")
    return {"subject": payload["sub"], "email": payload.get("email")}


def _token_set(body: dict, previous: dict | None = None) -> dict:
    scopes = body["scope"].split() if isinstance(body.get("scope"), str) else (previous or {}).get("scopes", [])
    access = body.get("access_token")
    if not access or str(body.get("token_type", "")).lower() != "bearer":
        raise ProviderError("ChatGPT returned an unexpected token response.")
    refresh = body.get("refresh_token") or (previous or {}).get("refreshToken")
    if not refresh:
        raise ProviderError("ChatGPT returned no refresh token.")
    return {
        "accessToken": access,
        "refreshToken": refresh,
        "idToken": body.get("id_token") or (previous or {}).get("idToken"),
        "scopes": scopes,
        "expiresAt": time.time() + float(body.get("expires_in", 3600)),
    }


# --- login --------------------------------------------------------------------
_login_lock = threading.Lock()
_login_status: dict = {"pending": False, "error": None}


def status() -> dict:
    acct = _load().get("account")
    return {
        "connected": bool(acct and acct.get("tokens", {}).get("accessToken")),
        "email": (acct or {}).get("email"),
        "pending": _login_status["pending"],
        "error": _login_status["error"],
        "planScopes": bool(acct and all(s in acct["tokens"].get("scopes", []) for s in SCOPES)),
    }


def start_login(open_browser: bool = True) -> str:
    """Start the PKCE flow in a background thread. Returns the authorize URL."""
    if not _login_lock.acquire(blocking=False):
        raise ProviderError("A ChatGPT sign-in is already pending.")
    state = _load()
    verifier, nonce, expected_state = _b64url(secrets.token_bytes(32)), _b64url(secrets.token_bytes(32)), _b64url(secrets.token_bytes(32))
    result: dict = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence
            pass

        def do_GET(self):
            u = urlparse(self.path)
            if u.path != "/auth/callback" or done.is_set():
                self.send_response(404)
                self.end_headers()
                return
            q = parse_qs(u.query)
            if any(len(q.get(k, [])) > 1 for k in ("state", "code", "client_id", "error")) or q.get("state", [None])[0] != expected_state:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"Sign-in could not be verified.")
                return
            result.update({k: v[0] for k, v in q.items()})
            done.set()
            self.send_response(200)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write("Signed in. Return to LanguageHarness; you can close this tab.".encode())

    server = HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/auth/callback"
    acct = state.get("account")
    params = {
        "client_id": acct["clientId"] if acct else "dynamic_agent_client",
        **({} if acct else {"agent_name_hint": "LanguageHarness"}),
        "ext_agent_host_id": state["hostId"],
        **({"login_hint": acct["email"]} if acct and acct.get("email") else {}),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": " ".join(SCOPES),
        "resource": RESOURCE,
        "state": expected_state,
        "nonce": nonce,
        "code_challenge_method": "S256",
        "code_challenge": _b64url(hashlib.sha256(verifier.encode()).digest()),
    }
    url = f"{AUTHORIZE}?{urlencode(params)}"
    _login_status.update(pending=True, error=None)

    def run():
        try:
            server.timeout = 1
            deadline = time.time() + LOGIN_TIMEOUT
            while not done.is_set() and time.time() < deadline:
                server.handle_request()
            if not done.is_set():
                raise ProviderError("ChatGPT sign-in timed out.")
            if "error" in result:
                raise ProviderError("ChatGPT sign-in cancelled." if result["error"] == "access_denied" else f"ChatGPT sign-in failed: {result['error']}")
            client_id = result.get("client_id") or (acct or {}).get("clientId")
            if not client_id or client_id == "dynamic_agent_client" or not result.get("code"):
                raise ProviderError("ChatGPT sign-in returned no client id / code.")
            body = _grant({"grant_type": "authorization_code", "client_id": client_id, "code": result["code"], "code_verifier": verifier, "redirect_uri": redirect_uri})
            if not isinstance(body.get("id_token"), str):
                raise ProviderError("ChatGPT sign-in returned no id_token.")
            ident = _verify_id_token(body["id_token"], client_id, nonce)
            if acct and acct.get("subject") and acct["subject"] != ident["subject"] and acct["clientId"] == client_id:
                raise ProviderError("Signed in as a different account than the saved one.")
            st = _load()
            st["account"] = {"clientId": client_id, "issuer": ISSUER, **ident, "tokens": _token_set(body)}
            _save(st)
            if not all(s in st["account"]["tokens"]["scopes"] for s in SCOPES):
                _login_status["error"] = "Signed in, but ChatGPT plan permission is missing."
        except Exception as e:  # noqa: BLE001 - surfaced to UI
            _login_status["error"] = str(e)
        finally:
            server.server_close()
            _login_status["pending"] = False
            _login_lock.release()

    threading.Thread(target=run, daemon=True).start()
    if open_browser:
        webbrowser.open(url)
    return url


def logout() -> None:
    st = _load()
    acct = st.pop("account", None)
    _save(st)
    if acct:
        try:  # best-effort revocation
            cfg = httpx.get(f"{ISSUER}/.well-known/openid-configuration", timeout=5).json()
            if cfg.get("revocation_endpoint"):
                httpx.post(cfg["revocation_endpoint"], data={"token": acct["tokens"]["refreshToken"], "token_type_hint": "refresh_token", "client_id": acct["clientId"]}, timeout=5)
        except Exception:  # noqa: BLE001
            pass


_refresh_lock = threading.Lock()


def access_token() -> str:
    with _refresh_lock:
        st = _load()
        acct = st.get("account")
        if not acct:
            raise ProviderError("ChatGPT is not connected. Sign in under Settings.")
        tok = acct["tokens"]
        if tok.get("expiresAt", 0) - 60 < time.time():
            body = _grant({"grant_type": "refresh_token", "client_id": acct["clientId"], "refresh_token": tok["refreshToken"]})
            acct["tokens"] = _token_set(body, tok)
            _save(st)
            tok = acct["tokens"]
        return tok["accessToken"]


class ChatGPTProvider:
    name = "chatgpt"

    def _inner(self) -> OpenAIProvider:
        return OpenAIProvider(api_key=access_token(), base_url=RESOURCE, api_style="responses", stateless_stream_only=True)

    async def stream_chat(self, model: str, messages: list[Message]) -> AsyncIterator[str]:
        async for piece in self._inner().stream_chat(model, messages):
            yield piece

    async def complete_json(self, model: str, messages: list[Message], schema: dict) -> dict:
        return await self._inner().complete_json(model, messages, schema)

    async def list_models(self) -> list[str]:
        try:
            tok = access_token()
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{RESOURCE}/models", headers={"Authorization": f"Bearer {tok}"})
            body = r.json()
            if isinstance(body.get("models"), list):  # ChatGPT account catalog shape
                return [m["slug"] for m in body["models"] if m.get("visibility") == "list" and m.get("slug")]
            return sorted(m["id"] for m in body.get("data", []))
        except Exception:  # noqa: BLE001
            return []
