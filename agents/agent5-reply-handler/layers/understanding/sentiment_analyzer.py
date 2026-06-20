"""
Sentiment analyzer (Understanding Layer #2).

Lightweight, rule-based polarity scoring. Sentiment feeds escalation (angry/upset
leads go to a human) and tone-matching, so it doesn't need an LLM round-trip — a
lexicon of positive/negative cues with negation awareness is enough and is
instant + deterministic.
"""

from __future__ import annotations

import re

from ...models import IncomingReply, SentimentResult

_POSITIVE = {
    "interested", "great", "thanks", "thank", "love", "perfect", "awesome",
    "sounds good", "yes", "definitely", "happy", "glad", "appreciate", "helpful",
    "let's", "sure", "excited",
}
_NEGATIVE = {
    "not interested", "no thanks", "stop", "unsubscribe", "annoying", "spam",
    "angry", "frustrated", "waste", "never", "remove me", "harass", "report",
    "disappointed", "terrible", "rude", "leave me alone", "scam",
}
_NEGATORS = {"not", "no", "never", "don't", "isn't", "won't", "can't"}


def analyze(reply: IncomingReply) -> SentimentResult:
    text = (reply.clean_body or reply.raw_body).lower()
    if not text:
        return SentimentResult(sentiment="neutral", score=0.0)

    score = 0
    for phrase in _POSITIVE:
        if phrase in text:
            score += 1
    for phrase in _NEGATIVE:
        if phrase in text:
            score -= 1.5  # weight negatives — they drive escalation

    # Crude negation flip: "not interested" already handled as a phrase, but catch
    # "not great" style constructions.
    tokens = re.findall(r"[a-z']+", text)
    for i, tok in enumerate(tokens[:-1]):
        if tok in _NEGATORS and tokens[i + 1] in {"great", "good", "interested", "happy"}:
            score -= 2

    normalized = max(-1.0, min(1.0, score / 3.0))
    if normalized > 0.15:
        label = "positive"
    elif normalized < -0.15:
        label = "negative"
    else:
        label = "neutral"
    return SentimentResult(sentiment=label, score=round(normalized, 2))
