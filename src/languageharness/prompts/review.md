Write an end-of-session review for a Japanese learner ({level}) after a conversation about "{topic}".

You are given the transcript, per-message corrections, and the target words (from the learner's Anki deck) with whether they used them.

Return:
- "summary": 2–4 sentences in English: how the conversation went, what the learner did well.
- "recurring_mistakes": grouped patterns (not one-off typos): each with "pattern", "examples" (list of "wrong → right"), "tip".
- "strengths": 1–3 short items.
- "new_words": 5–12 useful words/expressions that appeared in the PARTNER's messages that are NOT in the target list and that the learner probably doesn't know yet at this level: "word", "reading", "meaning", "example" (the sentence from the transcript).
- "focus_next": 2–3 concrete things to practise next time.

Target words:
{targets}

Corrections:
{corrections}

Transcript:
{transcript}
