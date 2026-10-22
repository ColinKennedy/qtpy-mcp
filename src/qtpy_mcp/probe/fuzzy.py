"""Pure-Python fuzzy text scoring used to find widgets by description.

Nothing in here imports Qt, so it is cheap to test and safe to import anywhere.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass

__all__ = [
    "FIELD_WEIGHTS",
    "FieldMatch",
    "best_match",
    "normalize",
    "score",
]

# How much a hit in each widget field is trusted. A label's own text is the
# strongest signal; QSS and class names are weak hints.
FIELD_WEIGHTS: dict[str, float] = {
    "text": 1.0,
    "title": 1.0,  # QGroupBox title, QDockWidget/QMdiSubWindow window title
    "placeholder": 0.95,
    "accessible_name": 0.95,
    "object_name": 0.9,
    "window_title": 0.9,
    "tooltip": 0.9,
    "whats_this": 0.85,
    "status_tip": 0.85,
    "accessible_description": 0.85,
    "tab_text": 0.8,
    "properties": 0.7,
    "class_name": 0.6,
    "style_sheet": 0.5,
}

# Fields whose values can be long; skip the quadratic difflib pass on them.
_EXACT_ONLY_FIELDS = frozenset({"style_sheet", "whats_this", "properties"})
_MAX_FUZZY_LEN = 300
_MAX_CANDIDATE_LEN = 4000

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD = re.compile(r"[^\w\s]|_", re.UNICODE)


def normalize(text: str) -> str:
    """Lower-case, split camelCase, drop punctuation (``"Name:"`` -> ``"name"``)."""
    text = _CAMEL.sub(" ", text)
    text = _NON_WORD.sub(" ", text.casefold())
    return " ".join(text.split())


def score(query: str, candidate: str, *, fuzzy: bool = True) -> float:
    """Similarity in ``[0, 1]`` between a description and some widget text."""
    q = normalize(query)
    c = normalize(candidate[:_MAX_CANDIDATE_LEN])
    if not q or not c:
        return 0.0
    if q == c:
        return 1.0
    if q in c:
        # The description appears inside the candidate text.
        return 0.85 + 0.14 * (len(q) / len(c))
    if len(c) >= 3 and c in q:
        # The candidate text appears inside a longer description.
        return 0.6 + 0.3 * (len(c) / len(q))

    q_tokens = q.split()
    c_tokens = c.split()
    c_set = set(c_tokens)
    overlap = sum(1 for t in q_tokens if t in c_set) / len(q_tokens)
    best = 0.8 * overlap
    if fuzzy and len(c) <= _MAX_FUZZY_LEN and len(q) <= _MAX_FUZZY_LEN:
        best = max(best, difflib.SequenceMatcher(None, q, c).ratio())
        per_token = [
            max(difflib.SequenceMatcher(None, t, ct).ratio() for ct in c_tokens) for t in q_tokens
        ]
        best = max(best, 0.9 * (sum(per_token) / len(per_token)))
    return min(best, 0.99)


@dataclass(frozen=True)
class FieldMatch:
    """The best-scoring field for a query."""

    field: str
    value: str
    score: float


def best_match(
    query: str,
    fields: Mapping[str, str],
    *,
    only: Collection[str] | None = None,
) -> FieldMatch | None:
    """Score ``query`` against every (allowed) field and return the winner."""
    best: FieldMatch | None = None
    for name, value in fields.items():
        if not value or (only is not None and name not in only):
            continue
        weight = FIELD_WEIGHTS.get(name, 0.5)
        raw = score(query, value, fuzzy=name not in _EXACT_ONLY_FIELDS)
        weighted = raw * weight
        if weighted > 0 and (best is None or weighted > best.score):
            best = FieldMatch(name, value, weighted)
    return best
