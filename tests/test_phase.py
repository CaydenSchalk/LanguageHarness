from languageharness.config import ModelSlot
from languageharness.session import ChatMessage, Session, compute_phase, director_note


def mk(**kw):
    return Session(topic="t", partner=ModelSlot(model="m"), utility=ModelSlot(model="m"), **kw)


def with_turns(s, k):
    s.messages = []
    for i in range(k):
        s.messages += [ChatMessage(role="assistant", content="a"), ChatMessage(role="user", content="u")]
    return s


def test_turn_budget_phases():
    s = mk(mode="turns", budget_turns=10)
    phases = [compute_phase(with_turns(s, k))[0] for k in range(11)]
    assert phases[0] == "opening"
    assert phases[1:8] == ["main"] * 7
    assert phases[8:10] == ["wrap_up", "wrap_up"]
    assert phases[10] == "closing"


def test_short_budget_still_wraps():
    s = mk(mode="turns", budget_turns=3)
    assert [compute_phase(with_turns(s, k))[0] for k in range(4)] == ["opening", "main", "wrap_up", "closing"]


def test_time_budget():
    s = with_turns(mk(mode="time", budget_minutes=10, started_at=1000.0), 2)
    assert compute_phase(s, now=1000 + 5 * 60)[0] == "main"
    assert compute_phase(s, now=1000 + 8.5 * 60)[0] == "wrap_up"
    assert compute_phase(s, now=1000 + 10 * 60)[0] == "closing"


def test_goal_mode_wraps_after_goal_met_and_has_cap():
    s = with_turns(mk(mode="goal", goal="order", goal_max_turns=20, brief={"goal_checkpoints": ["a", "b"]}), 3)
    assert compute_phase(s)[0] == "main"
    s.goal_met_turn = 3
    assert compute_phase(s)[0] == "wrap_up"  # reply to turn 3 (detected after)
    with_turns(s, 4)
    assert compute_phase(s)[0] == "wrap_up"
    with_turns(s, 5)
    assert compute_phase(s)[0] == "closing"
    s2 = with_turns(mk(mode="goal", goal_max_turns=6, brief={"goal_checkpoints": ["a"]}), 6)
    assert compute_phase(s2)[0] == "closing"


def test_force_close():
    s = with_turns(mk(mode="turns", budget_turns=30), 2)
    s.force_close = True
    assert compute_phase(s)[0] == "closing"


def test_director_note_mentions_remaining_and_unused():
    from languageharness.session import Target

    s = with_turns(mk(mode="turns", budget_turns=10, brief={"arc": ["x", "y"]}), 2)
    s.targets = [Target(expression="食べる"), Target(expression="行く", used=True)]
    phase, info = compute_phase(s)
    note = director_note(s, phase, info)
    assert "8 of 10" in note and "食べる" in note and "行く" not in note
