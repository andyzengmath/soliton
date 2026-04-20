# hallucination-ast

Deterministic AST hallucination pre-check for the Soliton PR review plugin.
Implements the Khati et al. 2026 pattern (arXiv [2601.19106](https://arxiv.org/abs/2601.19106)):
parse PR diffs into an AST, extract external symbol references, and validate them
against a live introspection KB built from the target repo's installed packages.

Hits are emitted at confidence 100 (no LLM inference). Misses forward to the
Opus-backed `agents/hallucination.md` for reasoning.

## Scope (v0.1)

- **Language**: Python only. TS / Go / Java / Ruby follow in 4b.x patches.
- **KB source**: `site-packages` of the interpreter running the checker.
  `node_modules`, `go.sum`, pom deps deferred.
- **Rules**: `identifier_not_found`, `signature_mismatch_arity`,
  `signature_mismatch_keyword`, `deprecated_identifier`.

## Install (dev)

```bash
pip install -e lib/hallucination-ast/[dev]
```

## Run

```bash
# from a file
python -m hallucination_ast --diff path/to/change.diff

# from stdin
git diff HEAD~1 | python -m hallucination_ast --diff -
```

Emits JSON on stdout:

```json
{
  "findings": [ { "rule": "identifier_not_found", "symbol": "requests.gett", ... } ],
  "unresolved": [ ... ],
  "stats": { "totalReferences": 12, "resolvedOk": 9, "resolvedBad": 2, "unresolved": 1, "wallMs": 87 }
}
```

Non-zero exit if any `critical`-severity finding was emitted.

## Test

```bash
cd lib/hallucination-ast
pytest
```

Running `test_khati_corpus.py` requires the WM-SEMERU replication package. See
`tests/fixtures/khati-2026/README.md` for fetch instructions.

## Design reference

Full design spec: [`lib/hallucination-ast.md`](../hallucination-ast.md). That
doc's TypeScript interface signatures are illustrative; this package is the
authoritative Python implementation (per the Phase 4b resume prompt).

## License

MIT.
