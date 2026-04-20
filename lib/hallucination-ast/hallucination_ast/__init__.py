"""Khati-style deterministic AST hallucination pre-check.

Implements the Khati et al. 2026 pattern (arXiv 2601.19106):
parse PR diffs into an AST, extract external symbol references, and
validate them against an introspection KB built from site-packages.
Emits confidence-100 findings for identifier_not_found,
signature_mismatch_arity, signature_mismatch_keyword, and
deprecated_identifier. Unresolved references forward to the LLM.
"""

__version__ = "0.1.0"
