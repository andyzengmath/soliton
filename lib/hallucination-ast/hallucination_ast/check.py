"""Rule implementations — the four Khati 2026 finding types.

Each rule is a pure function of (reference, resolution) → list[Finding]. No
network, no file I/O, no LLM. Findings are emitted at confidence=100; the
spec contractually promises zero false positives on the corpus, which means
every rule must bail out rather than guess when its signal is weak:

  - identifier_not_found: only when the KB confirms the module was inspectable
    AND the symbol was not present. KB-unknown ⇒ forward, not flag.
  - signature_mismatch_*: only when the signature is actually inspectable.
    C-extension callables without inspect.signature support are skipped.
  - signature_mismatch_arity: only when arg_count is not None (no *splat).
  - deprecated_identifier: only when PEP 702 __deprecated__ was set.
"""
from __future__ import annotations

import dataclasses
import inspect
import sys
import time
from typing import Protocol

from .resolve import KnowledgeBase, Resolution, resolve
from .similarity import closest_match
from .types import (
    AstExtractedReference,
    Finding,
    ImportInfo,
    Report,
    ReportStats,
)


_STDLIB_MODULES = frozenset(sys.stdlib_module_names)


def _is_builtin_name(name: str) -> bool:
    """Is `name` in the builtins namespace? Handles both dict and module
    representations of __builtins__ (differs between main and imported)."""
    b = __builtins__
    if isinstance(b, dict):
        return name in b
    return hasattr(b, name)


# --- per-reference rule application ---------------------------------------


def check_reference(
    ref: AstExtractedReference,
    resolution: Resolution,
) -> list[Finding]:
    """Apply the four rules to one (ref, resolution) pair."""
    if not resolution.known:
        # Can't check anything — forward to LLM. Returning [] preserves the
        # precision contract: we never invent findings on unknowns.
        return []

    if not resolution.found:
        return [_identifier_not_found(ref, resolution)]

    # Symbol resolved — run the signature and deprecation rules.
    findings: list[Finding] = []

    if resolution.signature is not None:
        arity = _check_arity(ref, resolution.signature)
        if arity is not None:
            findings.append(arity)
        for kw in _check_kwargs(ref, resolution.signature):
            findings.append(kw)

    if resolution.is_deprecated:
        findings.append(_deprecated(ref, resolution))

    return findings


# --- rule emitters --------------------------------------------------------


def _identifier_not_found(
    ref: AstExtractedReference,
    resolution: Resolution,
) -> Finding:
    # Compare the leaf (e.g. "gett") against the module's public names so
    # the suggested fix is a bare identifier, not the full dotted path.
    leaf = ref.symbol.rsplit(".", 1)[-1]
    suggested = closest_match(leaf, resolution.siblings) if resolution.siblings else None

    siblings_hint = ""
    if resolution.siblings:
        preview = ", ".join(resolution.siblings[:8])
        siblings_hint = f" Known siblings in the module include: {preview}."
    suggestion_hint = f" Did you mean '{suggested}'?" if suggested else ""
    return Finding(
        rule="identifier_not_found",
        severity="critical",
        file=ref.file,
        line=ref.line,
        symbol=ref.symbol,
        message=(
            f"Symbol '{ref.symbol}' does not exist in module '{ref.module}'."
            f"{suggestion_hint}{siblings_hint}"
        ),
        evidence=(
            f"Introspected module '{ref.module}' — dir() did not contain "
            f"the path '{ref.symbol}'."
        ),
        suggested_fix=suggested,
    )


def _check_arity(
    ref: AstExtractedReference,
    sig: inspect.Signature,
) -> Finding | None:
    if ref.arg_count is None:
        return None  # call used *splat — unverifiable

    min_required, max_allowed = _arity_bounds(sig)

    if ref.arg_count < min_required:
        return Finding(
            rule="signature_mismatch_arity",
            severity="critical",
            file=ref.file,
            line=ref.line,
            symbol=ref.symbol,
            message=(
                f"Call to '{ref.symbol}' passes {ref.arg_count} positional "
                f"argument(s) but the signature requires at least "
                f"{min_required}."
            ),
            evidence=f"inspect.signature: {sig}",
        )
    if max_allowed is not None and ref.arg_count > max_allowed:
        return Finding(
            rule="signature_mismatch_arity",
            severity="critical",
            file=ref.file,
            line=ref.line,
            symbol=ref.symbol,
            message=(
                f"Call to '{ref.symbol}' passes {ref.arg_count} positional "
                f"argument(s) but the signature accepts at most "
                f"{max_allowed}."
            ),
            evidence=f"inspect.signature: {sig}",
        )
    return None


def _check_kwargs(
    ref: AstExtractedReference,
    sig: inspect.Signature,
) -> list[Finding]:
    if not ref.kwargs:
        return []
    if _has_var_keyword(sig):
        return []
    accepted = _accepted_kwarg_names(sig)
    out: list[Finding] = []
    for kw in ref.kwargs:
        if kw in accepted:
            continue
        out.append(Finding(
            rule="signature_mismatch_keyword",
            severity="improvement",
            file=ref.file,
            line=ref.line,
            symbol=ref.symbol,
            message=(
                f"Call to '{ref.symbol}' passes keyword argument '{kw}' "
                f"which is not declared on the target signature."
            ),
            evidence=(
                f"inspect.signature: {sig}; accepted keyword names: "
                f"{sorted(accepted) if accepted else '(none)'}"
            ),
        ))
    return out


def _deprecated(
    ref: AstExtractedReference,
    resolution: Resolution,
) -> Finding:
    detail = resolution.deprecation_message or "this API is marked deprecated"
    return Finding(
        rule="deprecated_identifier",
        severity="improvement",
        file=ref.file,
        line=ref.line,
        symbol=ref.symbol,
        message=f"'{ref.symbol}' is deprecated: {detail}.",
        evidence="__deprecated__ attribute set on the resolved object (PEP 702).",
    )


# --- signature introspection helpers --------------------------------------


def _arity_bounds(sig: inspect.Signature) -> tuple[int, int | None]:
    """Return (min_required_positional, max_allowed_positional_or_None).

    Skips a leading `self`/`cls` parameter so we don't false-positive on
    unbound method access (`Class.method(x)` passes the instance implicitly
    in typical call-through-instance cases).
    """
    min_required = 0
    max_allowed = 0
    unbounded = False
    params = list(sig.parameters.values())

    # Skip leading self/cls heuristic.
    if params and params[0].name in ("self", "cls"):
        params = params[1:]

    for p in params:
        if p.kind is inspect.Parameter.VAR_POSITIONAL:
            unbounded = True
            continue
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            continue
        if p.kind is inspect.Parameter.KEYWORD_ONLY:
            continue
        # POSITIONAL_ONLY or POSITIONAL_OR_KEYWORD.
        if p.default is inspect.Parameter.empty:
            min_required += 1
        max_allowed += 1

    return min_required, None if unbounded else max_allowed


def _has_var_keyword(sig: inspect.Signature) -> bool:
    return any(
        p.kind is inspect.Parameter.VAR_KEYWORD
        for p in sig.parameters.values()
    )


def _accepted_kwarg_names(sig: inspect.Signature) -> set[str]:
    return {
        name
        for name, p in sig.parameters.items()
        if p.kind in (
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    }


# --- orchestration --------------------------------------------------------


def check_source(
    source: str,
    file_path: str,
    kb: KnowledgeBase,
) -> Report:
    """High-level: extract refs + imports from a full Python source, rewrite
    alias-qualified refs, flag references whose root module is never imported,
    then run the standard resolve + check pipeline on the rest.
    """
    from .extract import extract_from_source, extract_imports_info

    refs = extract_from_source(source, file_path)
    imports = extract_imports_info(source)
    rewritten, missing_findings = _apply_import_context(refs, imports)
    report = check_all(rewritten, kb)
    if missing_findings:
        report.findings = missing_findings + report.findings
    return report


def _apply_import_context(
    refs: list[AstExtractedReference],
    imports: ImportInfo,
) -> tuple[list[AstExtractedReference], list[Finding]]:
    """Return (refs_with_aliases_rewritten, missing_import_findings).

    Missing-import findings are emitted at most once per missing root so we
    don't double-count when the same alias is used on many lines. The
    corresponding references are dropped from the rewritten list — once a
    module is known to be un-bound, KB lookup would be meaningless.
    """
    rewritten: list[AstExtractedReference] = []
    missing_findings: list[Finding] = []
    already_flagged: set[str] = set()

    for ref in refs:
        # Never rewrite or flag imports themselves — they're the source of truth.
        if ref.kind == "import":
            rewritten.append(ref)
            continue

        # Module-less bare calls: out of scope for import-context rules
        # (covered by the future 'bare call without alias' follow-up).
        if ref.module is None:
            rewritten.append(ref)
            continue

        new_ref = _rewrite_alias(ref, imports.alias_to_module)
        mod_root = new_ref.module.split(".", 1)[0]

        if (
            mod_root not in imports.imported_roots
            and mod_root not in imports.alias_to_module
            and mod_root not in _STDLIB_MODULES
            and not _is_builtin_name(mod_root)
        ):
            if mod_root not in already_flagged:
                already_flagged.add(mod_root)
                missing_findings.append(Finding(
                    rule="identifier_not_found",
                    severity="critical",
                    file=ref.file,
                    line=ref.line,
                    symbol=mod_root,
                    message=(
                        f"Name '{mod_root}' is used but never imported or "
                        f"defined in this file."
                    ),
                    evidence=(
                        f"No top-level `import {mod_root}` or "
                        f"`import X as {mod_root}` statement found."
                    ),
                ))
            # Drop the ref — KB lookup is pointless when the name is unbound.
            continue

        rewritten.append(new_ref)

    return rewritten, missing_findings


def _rewrite_alias(
    ref: AstExtractedReference,
    alias_to_module: dict[str, str],
) -> AstExtractedReference:
    """If ref.module starts with an alias, substitute the canonical module."""
    if not ref.module:
        return ref
    root, sep, rest = ref.module.partition(".")
    if root not in alias_to_module:
        return ref
    canonical = alias_to_module[root]
    new_module = canonical + (("." + rest) if rest else "")

    # Also rewrite the leading portion of the dotted symbol.
    sym_root, sym_sep, sym_rest = ref.symbol.partition(".")
    if sym_root == root:
        new_symbol = canonical + (("." + sym_rest) if sym_sep else "")
    else:
        new_symbol = ref.symbol
    return dataclasses.replace(ref, module=new_module, symbol=new_symbol)


def check_all(
    refs: list[AstExtractedReference],
    kb: KnowledgeBase,
) -> Report:
    """Resolve every reference against the KB and apply rules. Returns a
    fully-populated Report including stats. wall_ms covers resolve+check time.
    """
    start = time.perf_counter()

    findings: list[Finding] = []
    unresolved: list[AstExtractedReference] = []
    resolved_ok = 0
    resolved_bad = 0

    for ref in refs:
        resolution = resolve(ref, kb)
        if not resolution.known:
            unresolved.append(ref)
            continue
        if resolution.found:
            resolved_ok += 1
        else:
            resolved_bad += 1
        findings.extend(check_reference(ref, resolution))

    wall_ms = int((time.perf_counter() - start) * 1000)

    return Report(
        findings=findings,
        unresolved=unresolved,
        stats=ReportStats(
            total_references=len(refs),
            resolved_ok=resolved_ok,
            resolved_bad=resolved_bad,
            unresolved=len(unresolved),
            wall_ms=wall_ms,
        ),
    )
