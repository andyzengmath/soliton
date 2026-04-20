"""Tests for hallucination_ast.similarity + its integration into check.py."""
from __future__ import annotations

import pytest

from hallucination_ast.resolve import Resolution
from hallucination_ast.types import AstExtractedReference


def _ref(symbol, kind="method"):
    return AstExtractedReference(
        kind=kind,
        file="a.py",
        line=1,
        column=0,
        symbol=symbol,
        module=symbol.split(".", 1)[0] if "." in symbol else symbol,
    )


# --- closest_match pure function ------------------------------------------


def test_closest_match_finds_single_char_typo():
    from hallucination_ast.similarity import closest_match

    assert closest_match("gett", ["get", "post", "put"]) == "get"


def test_closest_match_finds_two_char_typo():
    from hallucination_ast.similarity import closest_match

    # "conect" → "connect" is one insertion
    assert closest_match("conect", ["connect", "disconnect"]) == "connect"


def test_closest_match_respects_max_distance():
    from hallucination_ast.similarity import closest_match

    # "foobarbaz" is far from any of these; distance > 2 → None
    assert closest_match("foobarbaz", ["get", "post"], max_distance=2) is None


def test_closest_match_default_max_distance_is_two():
    from hallucination_ast.similarity import closest_match

    # "abcd" vs "wxyz": distance 4 → exceeds default 2 → None
    assert closest_match("abcd", ["wxyz"]) is None


def test_closest_match_max_distance_configurable():
    from hallucination_ast.similarity import closest_match

    # Distance 3: would be rejected by default 2, allowed by max=3
    assert closest_match("abc", ["abcxyz"], max_distance=3) == "abcxyz"


def test_closest_match_prefers_smallest_distance():
    from hallucination_ast.similarity import closest_match

    # "gett" → "get" is distance 1, "gets" is distance 1 too, "fetch" is 5.
    # Ties break alphabetically for determinism: "get" < "gets".
    assert closest_match("gett", ["gets", "get", "fetch"]) == "get"


def test_closest_match_empty_candidates_returns_none():
    from hallucination_ast.similarity import closest_match

    assert closest_match("anything", []) is None


def test_closest_match_exact_match_returns_it():
    """Defensive: if the target IS in candidates, return it (distance 0)."""
    from hallucination_ast.similarity import closest_match

    assert closest_match("get", ["get", "post"]) == "get"


def test_closest_match_is_case_sensitive():
    """Most Python libraries follow snake_case; case-insensitive suggestions
    would be noisy (GET vs get are different symbols in Python)."""
    from hallucination_ast.similarity import closest_match

    assert closest_match("GET", ["get", "post"]) is None


def test_closest_match_handles_empty_target():
    from hallucination_ast.similarity import closest_match

    assert closest_match("", ["get"]) is None  # distance 3 > 2


# --- integration with check.py -------------------------------------------


def test_identifier_not_found_populates_suggested_fix():
    from hallucination_ast.check import check_reference

    ref = _ref("requests.gett")
    res = Resolution(
        found=False, known=True,
        siblings=["get", "post", "put", "delete"],
    )
    findings = check_reference(ref, res)
    assert len(findings) == 1
    assert findings[0].rule == "identifier_not_found"
    assert findings[0].suggested_fix == "get"


def test_identifier_not_found_no_fix_when_nothing_close():
    from hallucination_ast.check import check_reference

    ref = _ref("requests.wildly_different_name")
    res = Resolution(
        found=False, known=True,
        siblings=["get", "post"],
    )
    findings = check_reference(ref, res)
    assert len(findings) == 1
    assert findings[0].suggested_fix is None


def test_identifier_not_found_uses_leaf_for_similarity_not_full_dotted():
    """suggestedFix should replace the leaf, not the whole dotted path —
    we compare "gett" against siblings, not "requests.gett"."""
    from hallucination_ast.check import check_reference

    ref = _ref("requests.gett")
    res = Resolution(
        found=False, known=True,
        siblings=["get"],  # bare leaf names only
    )
    findings = check_reference(ref, res)
    # suggested_fix is a bare leaf name ('get'), not the dotted path.
    assert findings[0].suggested_fix == "get"
