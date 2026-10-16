"""Small, local-only emotional-context signal for TinyTable.

This is intentionally a cautious heuristic, not a diagnostic or claim about a
person's internal state. It produces an inspectable signal that the local model may use
as optional response context.
"""

from __future__ import annotations

import re
from . import seats
from .seats import OPERATOR


POSITIVE = {
    "glad", "good", "great", "happy", "hope", "hopeful", "relieved", "thank", "thanks",
    "excited", "love", "loved", "proud", "calm", "helpful", "progress", "worked",
}
NEGATIVE = {
    "angry", "anxious", "bad", "blocked", "confused", "cry", "difficult", "disappointed",
    "exhausted", "frustrated", "hard", "hurt", "lonely", "overwhelmed", "sad", "scared",
    "stressed", "tired", "worry", "worried", "worse", "fail", "failed", "can't", "cannot",
}
SUPPORT = {
    "advice", "help", "how", "should", "stuck", "decision", "decide", "plan", "support",
    "what do i do", "where do i start",
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z']+", text.casefold()))


def assess(body: str, context: str = "") -> dict:
    """Return a bounded, non-diagnostic assessment suitable for local storage."""

    tokens = _tokens(body)
    positive_hits = sorted(tokens & POSITIVE)
    negative_hits = sorted(tokens & NEGATIVE)
    support_hits = sorted(tokens & SUPPORT)
    score = len(positive_hits) - len(negative_hits)
    if not positive_hits and not negative_hits:
        sentiment = "uncertain"
        confidence = 0.2
        uncertainty = "Insufficient textual evidence for a reliable sentiment signal."
    elif positive_hits and negative_hits:
        sentiment = "mixed"
        confidence = 0.35
        uncertainty = "Positive and negative language are both present; ask before interpreting."
    elif score > 0:
        sentiment = "positive"
        confidence = min(0.85, 0.45 + 0.1 * len(positive_hits))
        uncertainty = f"This is a language signal, not a claim about {seats.name(OPERATOR)}'s internal state."
    else:
        sentiment = "negative"
        confidence = min(0.85, 0.45 + 0.1 * len(negative_hits))
        uncertainty = f"This is a language signal, not a claim about {seats.name(OPERATOR)}'s internal state."

    if support_hits:
        context_label = "support_seeking"
    elif any(word in tokens for word in {"build", "code", "implement", "test", "deploy", "fix"}):
        context_label = "task_focus"
    elif any(word in tokens for word in {"feel", "feeling", "emotion", "emotional", "reflect"}):
        context_label = "reflection"
    else:
        context_label = "general_conversation"

    return {
        "sentiment": sentiment,
        "context": context_label,
        "confidence": round(confidence, 2),
        "uncertainty": uncertainty,
        "evidence": {"positive_terms": positive_hits, "negative_terms": negative_hits, "support_terms": support_hits},
        "recommendation_mode": "offer_options" if support_hits else "listen_and_clarify",
        "context_messages_considered": 1 if context else 0,
    }
