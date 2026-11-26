import json
import sqlite3

import httpx

from conftest import run
from languageharness.anki.ankiconnect import AnkiConnect
from languageharness.anki.base import guess_field_map, normalize_field, split_furigana
from languageharness.anki.sqlite_reader import SqliteAnki


def test_normalize_and_furigana():
    assert normalize_field("<b>食べる</b>&nbsp;[sound:x.mp3]") == "食べる"
    assert normalize_field("to eat<br>to consume") == "to eat; to consume"
    assert split_furigana("日本語[にほんご]を 勉強[べんきょう]する") == ("日本語を勉強する", "にほんごをべんきょうする")
    assert split_furigana("食[た]べ 物[もの]") == ("食べ物", "たべもの")
    assert split_furigana("たべる") == ("たべる", "")


def test_guess_field_map():
    assert guess_field_map(["Expression", "Reading", "Meaning", "Audio"]) == {"expression": "Expression", "reading": "Reading", "meaning": "Meaning"}
    assert guess_field_map(["Vocab-Kanji", "Vocab-Furigana", "Vocab-English", "Sentence"]) == {"expression": "Vocab-Kanji", "reading": "Vocab-Furigana", "meaning": "Vocab-English"}
    assert guess_field_map(["Front", "Back"]) == {"expression": "Front", "reading": "", "meaning": "Back"}
    core = ["Vocabulary-Kanji", "Vocabulary-Furigana", "Vocabulary-Kana", "Vocabulary-English", "Vocabulary-Audio", "Expression", "Reading", "Sentence-English"]
    assert guess_field_map(core) == {"expression": "Vocabulary-Kanji", "reading": "Vocabulary-Furigana", "meaning": "Vocabulary-English"}
    subs = ["ExpressionAudio", "Image", "Expression", "ExpressionMeaning", "Target", "TargetMeaning", "Grammar", "TargetPronunciation"]
    assert guess_field_map(subs) == {"expression": "Target", "reading": "TargetPronunciation", "meaning": "TargetMeaning"}
    mining = ["Target", "Sentence", "TargetReading", "Meaning", "PitchAccent", "SentenceReading", "SentenceMeaning"]
    assert guess_field_map(mining) == {"expression": "Target", "reading": "TargetReading", "meaning": "Meaning"}


def _legacy_collection(path):
    c = sqlite3.connect(path)
    c.executescript("""
      create table col (id integer primary key, ver int, decks text, models text);
      create table notes (id integer primary key, mid int, flds text);
      create table cards (id integer primary key, nid int, did int, odid int, ivl int, lapses int, type int, queue int);
      create table revlog (id integer primary key, cid int);
    """)
    decks = {"1": {"name": "Default"}, "2": {"name": "日本語::Core"}, "3": {"name": "Other"}}
    models = {"10": {"name": "JP", "flds": [{"name": "Word", "ord": 0}, {"name": "Reading", "ord": 1}, {"name": "Meaning", "ord": 2}]}}
    c.execute("insert into col values (1, 11, ?, ?)", (json.dumps(decks), json.dumps(models)))
    rows = [  # nid, flds, did, ivl, type, queue
        (100, "食べる\x1fたべる\x1fto eat", 2, 30, 2, 2),
        (101, "行く\x1fいく\x1fto go", 2, 3, 2, 2),
        (102, "新しい\x1fあたらしい\x1fnew", 2, 0, 0, 0),       # new -> excluded
        (103, "犬\x1fいぬ\x1fdog", 2, 10, 2, -1),              # suspended -> excluded
        (104, "猫\x1fねこ\x1fcat", 3, 10, 2, 2),                # other deck
    ]
    for i, (nid, flds, did, ivl, typ, q) in enumerate(rows):
        c.execute("insert into notes values (?, 10, ?)", (nid, flds))
        c.execute("insert into cards values (?, ?, ?, 0, ?, 0, ?, ?)", (1000 + i, nid, did, ivl, typ, q))
    c.execute("insert into revlog values (1700000000000, 1000)")
    c.commit()
    c.close()


def _modern_collection(path):
    c = sqlite3.connect(path)
    c.create_collation("unicase", lambda a, b: (a > b) - (a < b))
    c.executescript("""
      create table col (id integer primary key, ver int);
      create table decks (id integer primary key, name text collate unicase);
      create table notetypes (id integer primary key, name text collate unicase);
      create table fields (ntid int, ord int, name text collate unicase);
      create table notes (id integer primary key, mid int, flds text);
      create table cards (id integer primary key, nid int, did int, odid int, ivl int, lapses int, type int, queue int);
      create table revlog (id integer primary key, cid int);
      insert into col values (1, 18);
      insert into decks values (5, '日本語' || char(31) || 'Core');
      insert into notetypes values (20, 'Kaishi');
      insert into fields values (20, 0, 'Word'), (20, 1, 'Word Reading'), (20, 2, 'Word Meaning');
      insert into notes values (200, 20, '勉強' || char(31) || '勉強[べんきょう]' || char(31) || 'study');
      insert into cards values (2000, 200, 5, 0, 40, 1, 2, 2);
    """)
    c.commit()
    c.close()


def test_sqlite_legacy(tmp_path):
    p = tmp_path / "collection.anki2"
    _legacy_collection(p)
    r = SqliteAnki(p)
    assert r.deck_names() == ["Default", "Other", "日本語::Core"]
    items = r.known_items(["日本語"], {})
    assert {i.expression for i in items} == {"食べる", "行く"}
    eat = next(i for i in items if i.expression == "食べる")
    assert eat.status == "mature" and eat.reading == "たべる" and eat.last_review == 1700000000
    assert {i.expression for i in r.known_items([], {})} == {"食べる", "行く", "猫"}


def test_sqlite_modern(tmp_path):
    p = tmp_path / "collection.anki2"
    _modern_collection(p)
    r = SqliteAnki(p)
    assert r.deck_names() == ["日本語::Core"]
    assert r.note_types() == {"Kaishi": ["Word", "Word Reading", "Word Meaning"]}
    [item] = r.known_items(["日本語::Core"], {})
    assert (item.expression, item.reading, item.meaning, item.deck) == ("勉強", "べんきょう", "study", "日本語::Core")


def test_ankiconnect_known_items():
    calls = []

    def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        a = body["action"]
        result = {
            "findCards": [1, 2],
            "cardsInfo": [
                {"cardId": 1, "note": 11, "modelName": "JP", "deckName": "D", "interval": 25, "lapses": 0,
                 "fields": {"Expression": {"value": "<div>飲む</div>", "order": 0}, "Meaning": {"value": "to drink", "order": 1}}},
                {"cardId": 2, "note": 11, "modelName": "JP", "deckName": "D", "interval": 2, "lapses": 1,
                 "fields": {"Expression": {"value": "飲む", "order": 0}, "Meaning": {"value": "to drink", "order": 1}}},
            ],
            "getReviewsOfCards": {"1": [{"id": 1700000000000}], "2": [{"id": 1700000500000}]},
        }[a]
        return httpx.Response(200, json={"result": result, "error": None})

    ac = AnkiConnect(transport=httpx.MockTransport(handler))
    items = run(ac.known_items(["D"], {}))
    assert len(items) == 1  # deduped by expression
    it = items[0]
    assert it.expression == "飲む" and it.interval == 25 and it.last_review == 1700000500 and it.meaning == "to drink"
    assert calls[0]["params"]["query"] == '("deck:D") -is:new -is:suspended'
