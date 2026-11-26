import pytest

from conftest import collect, run
from languageharness import session as sess
from languageharness.config import ModelSlot
from languageharness.providers import ProviderError


def mk(**kw):
    s = sess.Session(topic="ラーメン", partner=ModelSlot(model="m"), utility=ModelSlot(model="m"), use_anki=False, **kw)
    sess.save(s)
    return s


def test_full_turn_flow_ends_on_budget(fake):
    s = mk(mode="turns", budget_turns=3)
    evs = run(collect(sess.prepare(s)))
    assert evs[-1]["event"] == "ready" and s.status == "ready" and s.brief["summary"]
    s.targets = [sess.Target(expression="美味しい", reading="おいしい"), sess.Target(expression="食べる", reading="たべる")]

    fake.replies = ["こんにちは！ラーメンは好きですか？", "いいですね。", "そろそろ…", "今日はありがとう。さようなら！"]
    run(collect(sess.open_conversation(s)))
    assert s.status == "active" and s.messages[0].phase == "opening"

    fake.correction = {"ok": False, "corrected": "はい、大好きです。", "notes": [{"original": "大好き", "fix": "大好きです", "explanation": "polite"}],
                       "target_words_used": ["食べる"], "goal_checkpoints_met": []}
    evs = run(collect(sess.user_message(s, "はい、大好き。よく食べます")))
    kinds = [e["event"] for e in evs]
    assert kinds[0] == "delta" and "correction" in kinds and kinds[-1] == "state"
    assert s.messages[1].correction["corrected"] == "はい、大好きです。"
    assert [t.used for t in s.targets] == [False, True]

    fake.correction = None
    run(collect(sess.user_message(s, "美味しいですね")))
    assert s.targets[0].used  # cheap substring pass
    assert s.messages[-1].phase == "wrap_up"
    run(collect(sess.user_message(s, "そうですね")))
    assert s.messages[-1].phase == "closing" and s.status == "ended"
    # last director note told the partner to close
    assert "phase: closing" in fake.chat_calls[-1][-1]["content"]

    with pytest.raises(ProviderError):
        run(collect(sess.user_message(s, "まだ話したい")))

    reloaded = sess.load(s.id)
    assert reloaded.status == "ended" and len(reloaded.messages) == 7

    from languageharness.review import make_review, new_words_tsv

    r = run(make_review(reloaded))
    assert r["targets_used"] == ["美味しい", "食べる"] and r["new_words"][0]["word"] == "替え玉"
    assert new_words_tsv(r["new_words"]).startswith("替え玉\tかえだま")


def test_goal_mode_closes_after_checkpoints(fake):
    s = mk(mode="goal", goal="order ramen", goal_max_turns=20)
    run(collect(sess.prepare(s)))
    assert s.checkpoints == ["order ramen", "ask the price"]
    run(collect(sess.open_conversation(s)))
    fake.correction = {"ok": True, "corrected": "", "notes": [], "target_words_used": [], "goal_checkpoints_met": ["order ramen", "ask the price"]}
    run(collect(sess.user_message(s, "豚骨ラーメンをください。いくらですか？")))
    assert s.goal_met_turn == 1
    fake.correction = None
    run(collect(sess.user_message(s, "ありがとう")))
    assert s.messages[-1].phase == "wrap_up"
    run(collect(sess.user_message(s, "はい")))
    assert s.status == "ended"


def test_close_now(fake):
    s = mk(mode="turns", budget_turns=30)
    s.status = "ready"
    run(collect(sess.open_conversation(s)))
    run(collect(sess.close_now(s)))
    assert s.status == "ended" and s.messages[-1].phase == "closing"


def test_failed_reply_is_not_saved(fake, monkeypatch):
    s = mk(mode="turns", budget_turns=5)
    run(collect(sess.open_conversation(s)))

    async def boom(*a, **k):
        raise ProviderError("down")
        yield  # pragma: no cover

    monkeypatch.setattr(fake, "stream_chat", boom)
    fresh = sess.load(s.id)
    with pytest.raises(ProviderError):
        run(collect(sess.user_message(fresh, "こんにちは")))
    assert len(sess.load(s.id).messages) == 1
