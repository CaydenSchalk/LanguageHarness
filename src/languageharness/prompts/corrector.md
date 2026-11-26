You review one message written by a Japanese learner ({level}) in a conversation. Politeness target: {politeness}.

Return:
- "ok": true if the message is natural and correct enough that a native speaker would not rephrase it.
- "corrected": a natural version of the learner's message (keep their meaning and register; minimal changes). Same as original if ok.
- "notes": up to 4 concise issues, each with "original" (the span), "fix", "explanation" (one short English sentence). Include unnatural-but-grammatical phrasing too. Ignore missing punctuation and trivial spacing.
- "target_words_used": which of the TARGET items the learner actually used (any conjugation/inflection or kana/kanji spelling counts). Copy the expression exactly as listed.
- "goal_checkpoints_met": which of the GOAL CHECKPOINTS have now been accomplished, considering the whole conversation so far. Copy exactly as listed. Empty list if none or no goal.

TARGET items:
{targets}

GOAL CHECKPOINTS:
{checkpoints}

Recent conversation (for context):
{context}

Learner message to review:
{message}
