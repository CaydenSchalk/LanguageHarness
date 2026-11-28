# LanguageHarness

A local harness for **Japanese conversation practice** with an LLM partner (Ollama, the OpenAI API, or a ChatGPT subscription).

- **Full conversation in Japanese.** The partner stays in character. Corrections appear in a side panel next to each of your messages, from a separate "utility" model.
- **Knows the topic.** Before you start, it researches the topic (Tavily web search) and writes a brief with facts, topic vocab and a conversation arc.
- **Always ends.** You choose a budget: **turns**, **minutes**, or a **goal** with checkpoints and a hard cap. The harness tracks the phase (`opening → main → wrap_up → closing`) and tells the partner in a hidden director note each turn. After the closing reply, input locks. **End** ends the conversation early with a natural goodbye.
- **Uses your Anki.** It reads your reviewed (non-new, non-suspended) cards from the decks you pick. It ranks them by maturity and how recently you reviewed them, and the model picks about 25 that fit the topic. The partner tries to get you to use them, and you get a ✓ when you do.
- **Review at the end:** recurring mistakes, strengths, target words used/missed, and new words from the conversation. You can download those as TSV or add them to Anki through AnkiConnect.

## Run

```bash
uv sync
uv run languageharness          # opens http://127.0.0.1:8000
```

Then open **Settings**:

1. **Models.** Set a *partner* model (the conversation) and a *utility* model (brief, corrections, word picking, review; must handle JSON output well).
   - Ollama: e.g. `ollama pull qwen3:14b` (fits a 16 GB GPU). Thinking is turned off by default for speed (`ollama_think` in `data/settings.json`).
   - OpenAI: paste an API key. *Base URL* also works for any OpenAI-compatible server.
   - ChatGPT: **Sign in** to run a browser PKCE login. This uses OpenAI's agent-login endpoints, so it may stop working if OpenAI changes them. The API-key path is independent.
2. **Tavily key** (optional) for web research. Without it, the brief comes from the model's own knowledge.
3. **Anki.** Click *Load decks & note types*, tick your decks, and check the field mapping (it is guessed from field names; tick *save* to pin a mapping).
   - **AnkiConnect** (Anki open with add-on `2055492159`) gives live data and allows adding new cards.
   - Otherwise it falls back to reading a temporary copy of `collection.anki2` (Flatpak and standard paths are auto-detected). It never writes to your collection.

## On your phone

The UI is a home-screen web app: a bottom tab bar, a full-screen chat, and a bottom sheet for target words and the brief. Open the HTTPS address in Safari, tap **Share**, then **Add to Home Screen**. It runs full screen. If you switch apps in the middle of a reply, the server still finishes the turn, and the reply appears when you come back.

ChatGPT sign-in has to be done in a browser on the server machine, because the login returns to `127.0.0.1` there.

To deploy to the Raspberry Pi, run `scripts/deploy-pi.sh`. It copies the code, reinstalls it into the Pi's venv and restarts `languageharness.service`. It doesn't touch the Pi's `data/` or `anki/` folders.

## Data

Everything is plain files under `data/` (override with `LH_DATA_DIR`), and the folder is gitignored:
- `settings.json`
- `auth.json`: API keys, chmod 600
- `chatgpt-credentials.json`: chmod 600
- `sessions/*.json`: transcript, corrections, brief, targets, review

Prompts are editable markdown in `src/languageharness/prompts/`. The files in `partner/` are concatenated in order.

## Dev

```bash
uv run pytest      # deterministic: fake provider, mock AnkiConnect, fixture collections
```

Env: `LH_PORT`, `LH_HOST`, `LH_NO_BROWSER=1`, `LH_DATA_DIR`. Don't use port 8765, because AnkiConnect uses it.
