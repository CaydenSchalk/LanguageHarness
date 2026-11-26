"""Paths, settings and secrets. Everything lives as plain JSON under the data dir."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("LH_DATA_DIR", ROOT / "data"))
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
STATIC_DIR = Path(__file__).resolve().parent / "static"


def data_path(*parts: str) -> Path:
    p = DATA_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def write_json_atomic(path: Path, obj, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        if private:
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


ProviderName = Literal["ollama", "openai", "chatgpt"]


class ModelSlot(BaseModel):
    provider: ProviderName = "ollama"
    model: str = ""


class FieldMap(BaseModel):
    expression: str = ""
    reading: str = ""
    meaning: str = ""


class AnkiSettings(BaseModel):
    source: Literal["auto", "ankiconnect", "sqlite"] = "auto"
    ankiconnect_url: str = "http://127.0.0.1:8765"
    collection_path: str = ""  # empty = auto-detect
    decks: list[str] = Field(default_factory=list)
    # note type name -> field mapping; missing types are guessed
    field_maps: dict[str, FieldMap] = Field(default_factory=dict)
    candidate_limit: int = 300
    target_count: int = 25


class Settings(BaseModel):
    partner: ModelSlot = Field(default_factory=ModelSlot)
    utility: ModelSlot = Field(default_factory=ModelSlot)
    ollama_base_url: str = "http://localhost:11434"
    ollama_think: bool | None = False  # disable "thinking" on qwen3/deepseek-r1 style models for speed; None = model default
    ollama_num_ctx: int = 16384  # Ollama's default context is too small for brief/word-selection prompts
    openai_base_url: str = ""  # empty = official API
    openai_api_style: Literal["auto", "responses", "chat"] = "auto"
    anki: AnkiSettings = Field(default_factory=AnkiSettings)
    research_enabled: bool = True
    tavily_max_results: int = 4


def settings_path() -> Path:
    return data_path("settings.json")


def load_settings() -> Settings:
    return Settings.model_validate(read_json(settings_path(), {}) or {})


def save_settings(s: Settings) -> None:
    write_json_atomic(settings_path(), s.model_dump())


# --- secrets: data/auth.json, env vars take precedence ------------------------
SECRET_KEYS = ("OPENAI_API_KEY", "TAVILY_API_KEY")


def auth_path() -> Path:
    return data_path("auth.json")


def get_secret(name: str) -> str:
    return os.environ.get(name) or (read_json(auth_path(), {}) or {}).get(name, "")


def set_secret(name: str, value: str) -> None:
    if name not in SECRET_KEYS:
        raise ValueError(name)
    cur = read_json(auth_path(), {}) or {}
    if value:
        cur[name] = value
    else:
        cur.pop(name, None)
    write_json_atomic(auth_path(), cur, private=True)


def secret_status() -> dict[str, bool]:
    return {k: bool(get_secret(k)) for k in SECRET_KEYS}
