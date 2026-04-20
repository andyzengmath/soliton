"""Type-shape tests for hallucination_ast.types.

Pins the dataclass surface so downstream modules (extract, resolve, check, cli)
can rely on a stable schema matching the TS interfaces in the spec.
"""
from __future__ import annotations

import dataclasses
from typing import get_args, get_type_hints

import pytest


def test_ref_kind_literal_covers_spec():
    """Spec (lib/hallucination-ast.md §Interface) declares 6 reference kinds."""
    from hallucination_ast.types import RefKind

    assert set(get_args(RefKind)) == {
        "import",
        "call",
        "method",
        "attribute",
        "type",
        "decorator",
    }


def test_rule_literal_covers_spec_four_plus_two_tracked():
    """Resume prompt ships 4 rules day-one; spec's TS union lists 6. Keep all 6
    in the Literal so future rules don't require a type rename."""
    from hallucination_ast.types import Rule

    assert set(get_args(Rule)) == {
        "identifier_not_found",
        "signature_mismatch_arity",
        "signature_mismatch_keyword",
        "deprecated_identifier",
        "unknown_attribute",
        "wrong_import_path",
    }


def test_severity_literal():
    from hallucination_ast.types import Severity

    assert set(get_args(Severity)) == {"critical", "improvement", "nitpick"}


def test_ast_extracted_reference_fields():
    from hallucination_ast.types import AstExtractedReference

    ref = AstExtractedReference(
        kind="call",
        file="foo.py",
        line=10,
        column=4,
        symbol="requests.get",
        module="requests",
        arg_count=1,
        kwargs=["timeout"],
        type_args=None,
    )
    assert dataclasses.is_dataclass(ref)
    assert ref.kind == "call"
    assert ref.symbol == "requests.get"
    assert ref.arg_count == 1
    assert ref.kwargs == ["timeout"]


def test_ast_extracted_reference_optional_fields_default_none():
    from hallucination_ast.types import AstExtractedReference

    ref = AstExtractedReference(
        kind="import",
        file="foo.py",
        line=1,
        column=0,
        symbol="requests",
    )
    assert ref.module is None
    assert ref.arg_count is None
    assert ref.kwargs is None
    assert ref.type_args is None


def test_finding_confidence_pinned_to_100():
    """Spec: 'confidence: 100 — always 100 for this library, deterministic'."""
    from hallucination_ast.types import Finding

    f = Finding(
        rule="identifier_not_found",
        severity="critical",
        file="foo.py",
        line=10,
        symbol="requests.gett",
        message="get_t does not exist on requests",
        evidence="dir(requests) had no 'gett'",
    )
    assert f.confidence == 100


def test_finding_suggested_fix_optional():
    from hallucination_ast.types import Finding

    f = Finding(
        rule="identifier_not_found",
        severity="critical",
        file="foo.py",
        line=10,
        symbol="requests.gett",
        message="m",
        evidence="e",
    )
    assert f.suggested_fix is None


def test_report_stats_nested_dataclass():
    from hallucination_ast.types import Report, ReportStats

    r = Report(
        findings=[],
        unresolved=[],
        stats=ReportStats(
            total_references=0,
            resolved_ok=0,
            resolved_bad=0,
            unresolved=0,
            wall_ms=0,
        ),
    )
    assert isinstance(r.stats, ReportStats)
    assert r.stats.total_references == 0


def test_report_to_dict_json_shape():
    """CLI emits JSON; verify asdict() produces camelCase keys matching the
    spec's TS interface so the hallucination agent can consume them."""
    from hallucination_ast.types import (
        AstExtractedReference,
        Finding,
        Report,
        ReportStats,
        report_to_json_dict,
    )

    r = Report(
        findings=[
            Finding(
                rule="identifier_not_found",
                severity="critical",
                file="f.py",
                line=3,
                symbol="requests.gett",
                message="m",
                evidence="e",
                suggested_fix="get",
            )
        ],
        unresolved=[
            AstExtractedReference(
                kind="call",
                file="f.py",
                line=4,
                column=0,
                symbol="unknown.thing",
            )
        ],
        stats=ReportStats(
            total_references=2,
            resolved_ok=0,
            resolved_bad=1,
            unresolved=1,
            wall_ms=42,
        ),
    )
    d = report_to_json_dict(r)
    assert "findings" in d and "unresolved" in d and "stats" in d

    stats = d["stats"]
    # camelCase per spec's TS interface
    assert stats["totalReferences"] == 2
    assert stats["resolvedOk"] == 0
    assert stats["resolvedBad"] == 1
    assert stats["unresolved"] == 1
    assert stats["wallMs"] == 42

    f = d["findings"][0]
    assert f["confidence"] == 100
    assert f["suggestedFix"] == "get"

    u = d["unresolved"][0]
    assert u["kind"] == "call"
    assert "argCount" in u or u.get("argCount") is None


def test_finding_type_hints_exportable():
    """get_type_hints resolves — catches missing __future__ import issues."""
    from hallucination_ast.types import Finding

    hints = get_type_hints(Finding)
    assert "rule" in hints
    assert "confidence" in hints
