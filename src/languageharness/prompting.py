"""Prompt assembly from markdown files (numbered files concatenated in order)."""
from __future__ import annotations

from pathlib import Path

from .config import PROMPTS_DIR


class _Safe(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def render(name: str, **vars) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8").format_map(_Safe(vars))


def render_dir(name: str, **vars) -> str:
    files = sorted(Path(PROMPTS_DIR / name).glob("*.md"))
    return "\n\n".join(f.read_text(encoding="utf-8").format_map(_Safe(vars)).strip() for f in files)
