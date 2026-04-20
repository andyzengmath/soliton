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
    """One external-symbol reference extracted from a Python source file.

    Fields:
        kind: Syntactic role (import / call / method / attribute / type /
            decorator).
        file: Source file path, relative to the repo root.
        line: 1-based line number in the post-image (tree-sitter's 0-indexed
            row + 1).
        column: 0-based column offset in the source line.
        symbol: Fully-qualified dotted name as it appears in source —
            e.g. "requests.get", "np.average", "json.dumps".
        module: Top-level package / module that owns the symbol
            (e.g. "requests" for "requests.get"). NONE for bare function
            calls with no dotted qualifier (e.g. `len(x)` → module=None,
            symbol="len"). When None, `resolve.resolve()` falls back to
            `_first_segment(symbol)` as the KB lookup key so bare calls
            are still checked against builtins / site-packages.
        arg_count: Positional argument count at the call site. NONE when
            the call uses `*splat` / `**splat` (arity statically
            unverifiable — downstream skips the arity rule).
        kwargs: Keyword argument names present at the call site. NONE for
            non-call kinds.
        type_args: Generic type parameters (reserved for future `type`
            kind; not populated in v0.1).
    """
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
    """Summary of every import statement (at any scope) in one source file.

    `alias_to_module` maps a locally-bound name back to its canonical
    target:

      - `import numpy as np`              →  alias_to_module["np"] = "numpy"
      - `from requests import get as g`   →  alias_to_module["g"] = "requests.get"

    Note the asymmetry: for `import X as Y` the target is a MODULE path;
    for `from X import Y as Z` the target is a MODULE.MEMBER path. Both
    forms are consumed by `check._rewrite_alias` which strips the alias
    prefix off the ref's symbol before KB lookup.

    `imported_roots` is the set of top-level names that have been bound
    by any form of import, including aliases. This set is the input to
    the missing-import heuristic — a reference whose root segment is
    NOT in imported_roots (and not a stdlib module and not a builtin
    and not a local variable) is flagged as an undefined name.

    For `from requests import get as http_get`:
      - `"requests"` goes into imported_roots (the module was loaded)
      - `"http_get"` goes into imported_roots (the local binding)
      - the bare `"get"` does NOT — it's shadowed by the alias
    """
    alias_to_module: dict[str, str] = field(default_factory=dict)
    imported_roots: set[str] = field(default_factory=set)


_SNAKE_TO_CAMEL = {
    # AstExtractedReference multi-word fields
    # (single-word fields — kind, file, line, column, symbol, module,
    #  kwargs — pass through unchanged and need no entry here):
    "arg_count": "argCount",
    "type_args": "typeArgs",
    # Finding multi-word fields (rule, severity, file, line, symbol,
    #  message, evidence, confidence — no entry needed):
    "suggested_fix": "suggestedFix",
    # ReportStats fields:
    "total_references": "totalReferences",
    "resolved_ok": "resolvedOk",
    "resolved_bad": "resolvedBad",
    "wall_ms": "wallMs",
}
# Invariant: every snake_case field (containing '_') in Finding,
# AstExtractedReference, ReportStats, and ImportInfo that is emitted by
# report_to_json_dict MUST appear above. When adding a new field, update
# this table in the same commit.


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
