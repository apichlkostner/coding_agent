"""Word statistics over plain text."""

from __future__ import annotations

import re
from collections import Counter

_WORD_RE = re.compile(r"[A-Za-z']+")


def _tokenize(text: str) -> list[str]:
    """Return lower-cased word tokens from ``text``."""
    return [match.group(0).lower() for match in _WORD_RE.finditer(text)]


def count_words(text: str) -> int:
    """Return the number of word tokens in ``text``."""
    return len(_tokenize(text))


def word_frequencies(text: str) -> dict[str, int]:
    """Return a mapping of word to occurrence count."""
    return dict(Counter(_tokenize(text)))


def most_common_word(text: str) -> str:
    """Return the most frequent word, or an empty string for empty input.

    Ties are broken by first appearance in the text.
    """
    tokens = _tokenize(text)
    if not tokens:
        return ""
    counts = Counter(tokens)
    best = tokens[0]
    for word in tokens:
        if counts[word] > counts[best]:
            best = word
    return best
