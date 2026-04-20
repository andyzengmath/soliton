"""Levenshtein-based fix suggester.

Spec §Non-goals: 'No fuzzy matching beyond Levenshtein distance ≤ 2.
Anything fuzzier is LLM territory.' Default max_distance honours that rule.

Pure Python; a ~20-line DP is fast enough — we only compare one candidate
identifier against ≲ a few hundred siblings from dir(module). No external
dependency, no compiled extension.
"""
from __future__ import annotations

from typing import Iterable


def closest_match(
    target: str,
    candidates: Iterable[str],
    max_distance: int = 2,
) -> str | None:
    """Return the candidate with the smallest Levenshtein distance to target,
    provided that distance is ≤ max_distance. Ties broken alphabetically for
    determinism. Returns None when no candidate qualifies or the candidates
    iterable is empty.
    """
    best: str | None = None
    best_distance: int | None = None
    for candidate in candidates:
        d = _levenshtein(target, candidate, cap=max_distance)
        if d > max_distance:
            continue
        if best_distance is None or d < best_distance or (
            d == best_distance and candidate < (best or "")
        ):
            best = candidate
            best_distance = d
    return best


def _levenshtein(a: str, b: str, cap: int) -> int:
    """Standard Wagner-Fischer DP with an early-exit `cap`.

    If the minimum value on any row already exceeds `cap`, return cap+1 —
    we don't need the exact distance for rejection.
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > cap:
        return cap + 1
    if la == 0:
        return lb
    if lb == 0:
        return la

    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        curr = [i] + [0] * lb
        row_min = i
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(
                prev[j] + 1,      # deletion
                curr[j - 1] + 1,  # insertion
                prev[j - 1] + cost,  # substitution
            )
            if curr[j] < row_min:
                row_min = curr[j]
        if row_min > cap:
            return cap + 1
        prev = curr
    return prev[lb]
