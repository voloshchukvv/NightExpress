"""Fuzzy file-name matching for "quick open": the query letters must appear in order."""

from __future__ import annotations

from collections.abc import Iterable

_SEPARATORS = "/\\_-. "


def fuzzy_score(query: str, candidate: str) -> int | None:
    """Higher is better; None when ``candidate`` does not contain the query as a subsequence.

    Letters that follow each other, start a word or sit in the file name (not in the folder)
    count more, so "mrs" prefers "src/main.rs" to "memory/readers.rs".
    """
    needle = query.replace(" ", "").lower()
    if not needle:
        return 0
    text = candidate.lower()
    name_start = max(text.rfind("/"), text.rfind("\\")) + 1
    score = 0
    position = 0
    previous = -2
    for char in needle:
        found = text.find(char, position)
        if found < 0:
            return None
        score += 1
        if found == previous + 1:
            score += 5
        if found == 0 or text[found - 1] in _SEPARATORS:
            score += 4
        if found >= name_start:
            score += 2
        previous = found
        position = found + 1
    if needle in text[name_start:]:
        score += 10
    return score * 100 - len(text)  # shorter paths win ties


def fuzzy_filter(query: str, candidates: Iterable[str], limit: int = 50) -> list[str]:
    """Candidates matching the query, best first; the input order breaks ties."""
    scored = []
    for order, candidate in enumerate(candidates):
        score = fuzzy_score(query, candidate)
        if score is not None:
            scored.append((-score if query.strip() else 0, order, candidate))
    scored.sort()
    return [candidate for _score, _order, candidate in scored[:limit]]
