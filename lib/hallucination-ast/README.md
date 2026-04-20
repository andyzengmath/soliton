# hallucination-ast

Deterministic AST hallucination pre-check for the Soliton PR review plugin.
Implements the pattern from Khati et al. 2026, arXiv
[2601.19106](https://arxiv.org/abs/2601.19106): parse each added Python
diff, extract external-symbol references, validate them against a live
introspection KB built from the target interpreter's installed packages,
and emit confidence-100 findings without a single LLM token.

Hits flow through `agents/hallucination.md` §2.5 and bypass the Opus
reasoning step entirely. Misses ("unresolved") forward to the normal
LLM pipeline.

**Shipping gate on the Khati 2026 200-sample public corpus:**

| Metric    | This package | Khati paper | Floor (Phase 4b resume prompt) |
|-----------|-------------:|------------:|-------------------------------:|
| Precision | **0.993**    | 1.000       | 0.95                           |
| Recall    | **0.944**    | 0.876       | 0.80                           |
| F1        | **0.968**    | 0.934       | —                              |

Reproduce: `python scripts/fetch_khati_corpus.py && pytest tests/test_khati_corpus.py -s`.

---

## Scope (v0.1)

- **Language:** Python only. TS/JS, Go, Java, Ruby follow in 4b.x patches.
- **KB source:** the interpreter's own `site-packages` (+ `sys.modules`
  for already-loaded modules). `node_modules`, `go.sum`, Maven pom deps
  are deferred.
- **Rules:** four deterministic findings, all at confidence 100:
  - `identifier_not_found` (critical) — symbol doesn't exist in the
    target module (e.g. `requests.gett` — typo for `get`).
  - `signature_mismatch_arity` (critical) — wrong positional argument
    count given a known signature.
  - `signature_mismatch_keyword` (improvement) — unknown kwarg passed
    and the signature has no `**kwargs` sink (e.g. `os.makedirs(path,
    recursive=True)` — `recursive` is a Node.js `fs.mkdir` idiom that
    doesn't exist in Python).
  - `deprecated_identifier` (improvement) — PEP 702 `__deprecated__`
    attribute set on the resolved object.

Two additional rule names (`unknown_attribute`, `wrong_import_path`)
are reserved in the `Rule` literal for future extension but are not
emitted in v0.1.

---

## Architecture

```
Unified diff (str)
  │
  ▼
┌─────────────────────┐
│ extract_from_diff   │  unidiff parse + per-file post-image load
│   (extract.py)      │     • safe path-containment check on repo_root
└─────────────────────┘        (rejects .., absolute, /dev/null, symlink
  │                             escapes — CWE-22 hardened)
  ▼
┌─────────────────────┐
│ extract_from_source │  tree-sitter-python AST walk
│   (extract.py)      │     • emits import / call / method / attribute refs
└─────────────────────┘        (line & column 1/0-indexed per spec)
  │
  ▼                     + extract_imports_info(source)
┌─────────────────────┐    → alias map + imported_roots set
│ _apply_import_      │
│   context           │  • alias rewrite (np.x → numpy.x)
│   (check.py)        │  • missing-import detection
└─────────────────────┘  • local-name shadow forwarding
  │
  ▼
┌─────────────────────┐
│ SitePackagesKB      │  importlib introspection with ALLOWLIST
│   (resolve.py)      │     • stdlib + curated packages may be freshly
│                     │       imported; unknown roots refuse (CWE-829)
│                     │     • caches per KB instance
│                     │     • distinguishes unbound methods from
└─────────────────────┘       staticmethods / classmethods
  │
  ▼
┌─────────────────────┐
│ check_reference     │  four rules, bail-out-on-weak-signal
│   (check.py)        │     • missing sig → skip arity + kwargs
│                     │     • *splat → skip arity
└─────────────────────┘     • PEP 702 check
  │
  ▼
┌─────────────────────┐
│ Report (JSON)       │  camelCase keys for cross-language consumer
│   (types.py)        │     • findings[] (rule, symbol, confidence=100, …)
│                     │     • unresolved[] (forward to LLM)
└─────────────────────┘     • stats (total / ok / bad / unresolved / wallMs)
```

---

## Install (dev)

```bash
pip install -e lib/hallucination-ast/[dev]
```

Deps pin to source-compatible Windows wheels:

- `tree-sitter >= 0.22`
- `tree-sitter-python >= 0.23`
- `click >= 8`
- `unidiff >= 0.7`

Dev additions: `pytest`, `pytest-cov`.

Python target: **3.11+**. Verified on CPython 3.14 Windows; no C
toolchain required.

---

## Run

```bash
# From a file
python -m hallucination_ast --diff path/to/change.diff

# From stdin (the form agents/hallucination.md §2.5 uses)
git diff HEAD~1 | python -m hallucination_ast --diff -

# With repo root for correct post-image line alignment on modified files
python -m hallucination_ast --diff /tmp/d.patch --repo-root /path/to/repo
```

### JSON output

```json
{
  "findings": [
    {
      "rule": "identifier_not_found",
      "severity": "critical",
      "file": "src/http_client.py",
      "line": 11,
      "symbol": "requests.gett",
      "message": "Symbol 'requests.gett' does not exist in module 'requests'. Did you mean 'get'?",
      "evidence": "Introspected module 'requests' — dir() did not contain the path 'requests.gett'.",
      "confidence": 100,
      "suggestedFix": "get"
    }
  ],
  "unresolved": [ /* forwarded to LLM */ ],
  "stats": {
    "totalReferences": 12,
    "resolvedOk": 9,
    "resolvedBad": 1,
    "unresolved": 2,
    "wallMs": 87
  }
}
```

### Exit codes

| Code | Meaning                                                           |
|-----:|-------------------------------------------------------------------|
|    0 | No critical finding (clean, or improvement / nitpick only)        |
|    1 | At least one critical finding emitted (signal — not a gate)       |
|    2 | Input error (missing diff file, malformed diff)                   |

---

## Public API

```python
from hallucination_ast import __version__
from hallucination_ast.types import (
    AstExtractedReference,  # the extracted ref dataclass
    Finding,                # emitted finding dataclass
    Report, ReportStats,    # aggregate + accounting
    ImportInfo,             # alias + imported-roots summary
    RefKind, Rule, Severity,  # Literal types
    report_to_json_dict,    # camelCase serialiser
)

from hallucination_ast.extract import (
    extract_from_source,    # str + file_path → list[AstExtractedReference]
    extract_from_diff,      # unified diff + repo_root → list[ref]
    extract_imports_info,   # source → ImportInfo (full-tree walk)
)

from hallucination_ast.resolve import (
    KnowledgeBase,          # Protocol[lookup(module, symbol) -> Resolution]
    SitePackagesKB,         # production implementation (importlib + allowlist)
    Resolution,             # found / known / signature / is_unbound_method / …
    resolve,                # (ref, kb) -> Resolution
)

from hallucination_ast.check import (
    check_reference,        # (ref, resolution) → list[Finding]
    check_all,              # (refs, kb) → Report
    check_source,           # (source, file_path, kb) → Report  (standalone .py)
    check_diff,             # (diff_text, repo_root, kb) → Report  (unified diff)
)

from hallucination_ast.similarity import closest_match
```

### Consumer-facing invariants

- **confidence is always 100.** This package never emits anything below
  certainty. If the signal is weak (module not installed, signature
  uninspectable, call uses `*splat`), the ref forwards to `unresolved`
  instead of firing a rule.
- **suggestedFix is a bare leaf name**, not a dotted path. E.g. for a
  typo'd `requests.gett`, `suggestedFix="get"`, not `"requests.get"`.
- **JSON keys are camelCase.** Internal Python fields are snake_case;
  `report_to_json_dict` translates via an explicit table (see
  `_SNAKE_TO_CAMEL` in `types.py`). Add new snake_case fields to the
  table in the same commit.

---

## Import-context rules (the non-trivial layer)

Simple call-site rules only catch typos. The Khati 2026 corpus mixes in
missing-import cases and alias-qualified calls, which need source-wide
context. `check_source` / `check_diff` add two phases before the rules
fire:

### 1. Alias rewrite

`import numpy as np; np.average(x)` — the extracted ref has
`module="np"`, which doesn't exist as a package. The rewrite phase
consults `ImportInfo.alias_to_module` and swaps it out:

```
np.average  →  numpy.average  (module="np" → module="numpy")
```

Handles `import X as Y`, `import X.Y as Z`, `from X import Y as Z`.
Aliases that resolve to something un-importable (`import nump as np`)
still forward to the LLM — we can't tell a typo-import from an internal
module.

### 2. Missing-import detection

If after alias rewrite the ref's root isn't:

- an imported module or alias, AND
- a Python builtin (`len`, `print`, …), AND
- a locally-bound name (function param, assignment target, for-var,
  with-as, except-as), AND
- a stdlib module

…then it's flagged as `identifier_not_found` with a message about being
"used but never imported or defined". Dedup is by root segment, so
`np.array` + `np.sum` + `np.mean` in a file without `import numpy as np`
produce ONE finding, not three.

Stdlib modules are NOT exempt from the missing-import check: `json.dumps`
without `import json` is a real runtime `NameError` and Khati's corpus
flags it as a hallucination. Real PRs loaded with `--repo-root` see the
existing import from disk and don't false-positive.

### 3. Local-name shadow forwarding (F2 — Phase 4b review)

Function parameters, assignments, and for-loop targets that name the
same thing as a package (e.g. `def func(df): df.to_csv(...)`,
`numbers = set(); numbers.add(1)`) are NOT flagged as missing imports.
But instead of silently dropping them, the refs are **forwarded to
`Report.unresolved`** so the downstream LLM layer can still catch cases
where a local binding is itself a hallucination (`time =
get_timestamp(); time.sleep(1)`).

---

## Security posture (Phase 4b review fixes — PR #26)

This package ingests attacker-controlled input (PR diffs on CI runners)
and so has to defend the following surfaces:

### Path traversal (CWE-22) — `_load_post_image`

An attacker-crafted diff header `b/../../etc/passwd.py` would previously
have made `repo_root / target` resolve outside the repo, and
`read_text()` would leak file contents into `Report.findings` /
`unresolved`. Gated by `_safe_join_within(root, rel)`:

- Rejects empty, `/dev/null`, NUL-byte, absolute, and drive-qualified paths.
- Rejects any path containing `..` segments.
- Calls `resolve(strict=False)` on both root and candidate, then
  `relative_to(root)` — any escape raises `ValueError` → `None` return,
  falling back to diff-synthesized post-image.

### Arbitrary-import RCE (CWE-829) — `SitePackagesKB._import_module`

`importlib.import_module(attacker_name)` runs the module's top-level
code with CI-runner privileges. Gated by a 3-tier allowlist:

1. **Already in `sys.modules`** — side effects ran at startup; safe.
2. **`sys.stdlib_module_names`** — safe to freshly import.
3. **Curated `_DEFAULT_ALLOWED_PACKAGES`** — Khati corpus libs + dev
   deps (numpy / pandas / matplotlib / requests / click / tree-sitter
   / unidiff / pytest / etc.). Configurable via `SitePackagesKB(
   allowed_packages=…)` — pass `frozenset()` to lock down further.

Anything else raises `ImportError`, the caller wraps it, and the ref
forwards with `known=False` — same as a genuinely missing package from
the LLM's perspective. No imports of attacker-chosen names.

### `KeyboardInterrupt` / `SystemExit` preserved

`_get_cached` catches `BaseException` to absorb broken-dependency
import errors, but re-raises `KeyboardInterrupt` and `SystemExit` so
user cancellation (`Ctrl+C`) and explicit `sys.exit()` propagate.

### Shell injection + TOCTOU — agent-side, not in this package

`agents/hallucination.md` §2.5 documents the safe invocation:
stdin-pipe form (no `/tmp` tempfile), quoted `"${VAR}"` positions, and
a mandatory refname regex gate on the `<base>` / `<head>` / `<repo>`
placeholders. See that file for the exact template.

---

## Testing

```bash
cd lib/hallucination-ast
pytest                          # ~150 unit + integration tests
pytest tests/test_khati_corpus.py -s  # shipping gate (needs fetch first)
```

### Khati 2026 shipping gate

The `test_khati_corpus.py` gate is the precision/recall ceiling. It's
**not** vendored because the upstream replication package has no
LICENSE:

```bash
python scripts/fetch_khati_corpus.py    # clones WM-SEMERU/Hallucinations-in-Code
pytest tests/test_khati_corpus.py -s
```

Thresholds (from `bench/crb/PHASE_4B_RESUME_PROMPT.md`):

- Precision ≥ 0.95 (floor) — currently 0.993
- Recall ≥ 0.80 (floor) — currently 0.944

The one remaining FP is a dataset labeling error (`pd.groupby(df)` is
marked "No hallucination" by Khati but pandas has no top-level
`groupby`). The remaining 9 FNs split across four deferred categories
— bare-call-without-alias, contextual misnaming, and two subtle typo
cases.

### Test layout

| File                      | What it covers                                |
|---------------------------|-----------------------------------------------|
| `test_types.py`           | Dataclass shapes + camelCase JSON round-trip  |
| `test_extract.py`         | Tree-sitter walker, diff parsing, path trav.  |
| `test_resolve.py`         | KB introspection, allowlist, sentinel, unbound-method  |
| `test_check.py`           | Rule engine in isolation with FakeKB          |
| `test_check_diff.py`      | Full pipeline at diff granularity             |
| `test_import_context.py`  | Alias rewrite, missing-import, local shadow   |
| `test_similarity.py`      | Levenshtein + wiring into check_reference     |
| `test_cli.py`             | CLI end-to-end via click's CliRunner          |
| `test_khati_corpus.py`    | Shipping gate (skips if fetch script not run) |

---

## Deferred for 4b.x

- **Other languages** — TypeScript / JavaScript (via `@babel/parser` or
  tree-sitter-javascript), Go (tree-sitter-go), Java (classpath-based
  resolution), Ruby (tree-sitter-ruby).
- **Bare-call-without-alias rule** — detect `read_csv("x.csv")` as a
  bare call with no local def + no import binding. Needs local-def
  extraction; currently out of scope.
- **`unknown_attribute` / `wrong_import_path` rules** — reserved in the
  `Rule` literal but not emitted.
- **Contextual misnaming** — `pd.read_excel("data.csv")` is valid
  Python but semantically wrong. LLM territory.

---

## Design reference

Full design spec: [`lib/hallucination-ast.md`](../hallucination-ast.md).
That document's TypeScript interface signatures are illustrative; this
package is the authoritative Python implementation (per the Phase 4b
resume prompt at `bench/crb/PHASE_4B_RESUME_PROMPT.md`).

## License

MIT.
