"""Read-only access to collection.anki2 (works with Anki closed). Always reads a temp copy."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .base import KnownItem, build_item, dedupe, guess_field_map


def candidate_collections() -> list[Path]:
    home = Path.home()
    roots = [
        home / ".local/share/Anki2",
        home / ".var/app/net.ankiweb.Anki/data/Anki2",
        home / "Library/Application Support/Anki2",
    ]
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        roots.append(Path(os.environ["APPDATA"]) / "Anki2")
    found = [p for r in roots if r.is_dir() for p in r.glob("*/collection.anki2")]
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)


def _unicase(a: str, b: str) -> int:
    a, b = a.casefold(), b.casefold()
    return (a > b) - (a < b)


@contextmanager
def open_copy(path: Path):
    with tempfile.TemporaryDirectory(prefix="lh-anki-") as tmp:
        dst = Path(tmp) / "collection.anki2"
        shutil.copy2(path, dst)
        for suffix in ("-wal", "-shm"):
            side = Path(str(path) + suffix)
            if side.exists():
                shutil.copy2(side, Path(str(dst) + suffix))
        conn = sqlite3.connect(dst)
        conn.create_collation("unicase", _unicase)
        try:
            yield conn
        finally:
            conn.close()


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}


def read_schema(conn) -> tuple[dict[int, str], dict[int, tuple[str, list[str]]]]:
    """Returns (deck id -> name, notetype id -> (name, ordered field names)) for old and new schemas."""
    if "notetypes" in _tables(conn):
        decks = {i: n.replace("\x1f", "::") for i, n in conn.execute("select id, name from decks")}
        types: dict[int, tuple[str, list[str]]] = {}
        names = dict(conn.execute("select id, name from notetypes"))
        for ntid, _ord, fname in conn.execute("select ntid, ord, name from fields order by ntid, ord"):
            types.setdefault(ntid, (names.get(ntid, str(ntid)), []))[1].append(fname)
        return decks, types
    decks_json, models_json = conn.execute("select decks, models from col").fetchone()
    decks = {int(k): v["name"] for k, v in json.loads(decks_json or "{}").items()}
    types = {
        int(k): (v["name"], [f["name"] for f in sorted(v["flds"], key=lambda f: f["ord"])])
        for k, v in json.loads(models_json or "{}").items()
    }
    return decks, types


def _in_decks(name: str, decks: list[str]) -> bool:
    return not decks or any(name == d or name.startswith(d + "::") for d in decks)


class SqliteAnki:
    def __init__(self, path: str | Path | None = None):
        if path:
            self.path = Path(path).expanduser()
        else:
            cands = candidate_collections()
            if not cands:
                raise FileNotFoundError("No Anki collection.anki2 found; set its path in Settings.")
            self.path = cands[0]
        if not self.path.exists():
            raise FileNotFoundError(f"{self.path} does not exist")

    def deck_names(self) -> list[str]:
        with open_copy(self.path) as conn:
            return sorted(read_schema(conn)[0].values())

    def note_types(self) -> dict[str, list[str]]:
        with open_copy(self.path) as conn:
            return {name: fields for name, fields in read_schema(conn)[1].values()}

    def known_items(self, decks: list[str], field_maps: dict[str, dict]) -> list[KnownItem]:
        with open_copy(self.path) as conn:
            deck_names, types = read_schema(conn)
            last = dict(conn.execute("select cid, max(id) from revlog group by cid"))
            rows = conn.execute(
                "select c.id, c.nid, c.did, c.odid, c.ivl, c.lapses, n.mid, n.flds from cards c join notes n on c.nid = n.id "
                "where c.type > 0 and c.queue != -1"
            )
            items: list[KnownItem] = []
            for cid, nid, did, odid, ivl, lapses, mid, flds in rows:
                deck = deck_names.get(odid or did, "")
                if not _in_decks(deck, decks):
                    continue
                tname, fnames = types.get(mid, ("", []))
                values = flds.split("\x1f")
                fields = dict(zip(fnames, values))
                fmap = field_maps.get(tname) or guess_field_map(fnames)
                lr = last.get(cid)
                it = build_item(card_id=cid, note_id=nid, note_type=tname, deck=deck, fields=fields, fmap=fmap,
                                interval=ivl, last_review=lr / 1000 if lr else None, lapses=lapses)
                if it:
                    items.append(it)
        return dedupe(items)
