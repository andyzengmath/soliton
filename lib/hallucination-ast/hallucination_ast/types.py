"""Type definitions for hallucination_ast.

Mirrors the TypeScript interfaces in lib/hallucination-ast.md §Interface,
translated to Python @dataclass with snake_case field names. The
report_to_json_dict helper emits camelCase keys for cross-language
compatibility with the Soliton agent's JSON consumer.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal


RefKind = Literal[
    "import",
    "call",
    "method",
    "attribute",
    "type",
    "decorator",
]


Rule = Literal[
    "identifier_not_found",
    "signature_mismatch_arity",
    "signature_mismatch_keyword",
    "deprecated_identifier",
    "unknown_attribute",
    "wrong_import_path",
]


Severity = Literal["critical", "improvement", "nitpick"]


@dataclass
class AstExtractedReference:
    kind: RefKind
    file: str
    line: int
    column: int
    symbol: str
    module: str | None = None
    arg_count: int | None = None
    kwargs: list[str] | None = None
    type_args: list[str] | None = None


@dataclass
class Finding:
    rule: Rule
    severity: Severity
    file: str
    line: int
    symbol: str
    message: str
    evidence: str
    confidence: int = 100
    suggested_fix: str | None = None


@dataclass
class ReportStats:
    total_references: int
    resolved_ok: int
    resolved_bad: int
    unresolved: int
    wall_ms: int


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    unresolved: list[AstExtractedReference] = field(default_factory=list)
    stats: ReportStats = field(
        default_factory=lambda: ReportStats(0, 0, 0, 0, 0)
    )


@dataclass
class ImportInfo:
    """Summary of top-level imports in one source file.

    alias_to_module maps a local name back to the canonical module path so
    check_source can rewrite `np.average` → `numpy.average` before handing
    the reference to the KB.

    imported_roots is the set of top-level names bound by any form of
    import (including `from X import Y`'s `Y`), used by the
    missing-import heuristic to decide whether a referenced module was
    ever brought into scope.
    """
    alias_to_module: dict[str, str] = field(default_factory=dict)
    imported_roots: set[str] = field(default_factory=set)


_SNAKE_TO_CAMEL = {
    "arg_count": "argCount",
    "type_args": "typeArgs",
    "suggested_fix": "suggestedFix",
    "total_references": "totalReferences",
    "resolved_ok": "resolvedOk",
    "resolved_bad": "resolvedBad",
    "wall_ms": "wallMs",
}


def _camelize(d: dict) -> dict:
    return {_SNAKE_TO_CAMEL.get(k, k): v for k, v in d.items()}


def report_to_json_dict(report: Report) -> dict:
    """Render a Report into the JSON shape the spec promises the agent.

    Keys are camelCased where the TS interface is camelCase so downstream
    consumers don't need to know about the Python snake_case internals.
    """
    return {
        "findings": [_camelize(asdict(f)) for f in report.findings],
        "unresolved": [_camelize(asdict(r)) for r in report.unresolved],
        "stats": _camelize(asdict(report.stats)),
    }
