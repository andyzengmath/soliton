# Deterministic AST Hallucination Pre-Check

Library spec implementing the Khati et al. 2026 pattern (arXiv 2601.19106) for
**zero-LLM** detection of API hallucinations in PR diffs. Reported empirical performance
on Python samples: **100 % precision, 87.6 % recall, F1 = 0.934**.

This library runs as a pre-check **inside** the `hallucination` agent (`agents/hallucination.md`).
Hits produced here bypass the LLM entirely with confidence 100; misses fall through to the
Opus-backed reasoning.

**Goal**: absorb the 80 % of hallucination cases that are "does this function exist?" style,
leaving the LLM to handle the genuinely ambiguous cases (wrong signature in valid API, config
key misuse, deprecated-but-present API, etc.).

## Why this matters

Hallucination detection is currently the single most expensive agent in Soliton's pipeline
(Opus model, every call). Khati 2026 shows a deterministic AST-driven checker achieves:

- **100 % precision** — zero false positives on their test set (161 hallucinated + 39 clean
  Python samples).
- **87.6 % recall** — catches ~88 % of real hallucinations without any LLM inference.
- **77 % auto-correction** — when a hallucinated identifier has a close match in the target
  library, the tool can propose the correct one.

Running this first saves Opus tokens on clean PRs and raises confidence on the catches the
LLM would eventually make anyway.

## Design — high level

```
Diff
  │
  ▼
 AST parse (tree-sitter per language)
  │
  ▼
 Extract new external references:
  - imports
  - function calls
  - method calls
  - attribute access
  - type annotations
  - decorator usage
  │
  ▼
 Resolve each reference against library introspection KB:
  - For each import, locate the package in node_modules / site-packages / …
  - For each call, check the package's exported symbols at the exact version
  - For each method, check the class definition for method name + signature
  │
  ▼
 Emit findings:
  - identifier_not_found (critical, 100 confidence)
  - signature_mismatch_arity (critical, 100 confidence)
  - signature_mismatch_keyword (improvement, 100 confidence)
  - deprecated_identifier (improvement, 100 confidence)
```

## Interface (TypeScript types — matches the plugin's runtime language)

```ts
// lib/hallucination-ast/types.ts
export type Language = 'python' | 'typescript' | 'javascript' | 'go' | 'rust' | 'java';

export interface AstExtractedReference {
  kind: 'import' | 'call' | 'method' | 'attribute' | 'type' | 'decorator';
  file: string;
  line: number;
  column: number;
  symbol: string;              // e.g. 'requests.get', 'fs.readFileAsync'
  module?: string;             // e.g. 'requests', 'fs'
  argCount?: number;
  kwargs?: string[];
  typeArgs?: string[];
}

export interface Finding {
  rule: 'identifier_not_found'
      | 'signature_mismatch_arity'
      | 'signature_mismatch_keyword'
      | 'deprecated_identifier'
      | 'unknown_attribute'
      | 'wrong_import_path';
  severity: 'critical' | 'improvement' | 'nitpick';
  confidence: 100;             // always 100 for this library — deterministic
  file: string;
  line: number;
  symbol: string;
  message: string;
  suggestedFix?: string;       // closest match if confidence > threshold
  evidence: string;            // e.g. "Searched node_modules/requests/__init__.pyi — no 'get_async' export"
}

export interface Report {
  findings: Finding[];
  unresolved: AstExtractedReference[];  // references we couldn't confirm either way → forward to LLM
  stats: {
    totalReferences: number;
    resolvedOk: number;
    resolvedBad: number;
    unresolved: number;
    wallMs: number;
  };
}

export function checkDiff(params: {
  diff: string;
  files: string[];
  repoRoot: string;
  language?: Language;       // auto-detect if absent
  knowledge?: KnowledgeBase; // overridable for tests
}): Promise<Report>;
```

## Language backends

### Python (primary — Khati 2026 implementation language)

- **Parser**: `tree-sitter-python` (already in graph-code-indexing's `package.json`).
- **Knowledge base**:
  - `site-packages/<pkg>/` for installed packages.
  - `.pyi` stub files where available (rich type info).
  - `dir()` / `getattr()` introspection fallback via an out-of-process subprocess (a small
    throwaway Python process that imports the package and dumps `dir(module)` to JSON).
- **Resolution strategy**:
  1. Locate the package for the import (e.g., `requests` → `site-packages/requests/`).
  2. For a call like `requests.get_async(...)`, check if `get_async` is in `dir(requests)`.
  3. If missing: emit `identifier_not_found`, severity critical. Compute Levenshtein to the
     nearest known identifier; propose `get` if distance ≤ 2.
  4. If present but arity mismatches: emit `signature_mismatch_arity` via `inspect.signature`.
  5. If `@deprecated` decorator on the target: emit `deprecated_identifier`.

### TypeScript / JavaScript

- **Parser**: `@babel/parser` (already a dep).
- **Knowledge base**:
  - `node_modules/<pkg>/package.json` → `types` / `typings` field → `.d.ts`.
  - TS compiler API for type resolution.
  - Fallback: read the package's `main` entrypoint and enumerate `module.exports`.
- **Resolution strategy**:
  1. Resolve the import path via Node's resolution algorithm.
  2. Load the `.d.ts` AST; check the referenced identifier exists.
  3. Check signature compatibility using TS types when available.
  4. Fallback to JS runtime-dump introspection (disabled by default; opt-in per-repo).

### Go

- **Parser**: `go/parser` equivalent via tree-sitter-go.
- **Knowledge**: vendored / GOPATH introspection; `go doc -all <pkg>` as a subprocess fallback.

### Rust

- **Parser**: tree-sitter-rust.
- **Knowledge**: `cargo metadata` to locate crates; crate docs / `.rmeta` when available.

### Java

- **Parser**: tree-sitter-java.
- **Knowledge**: classpath-based resolution; `javap` fallback for bytecode introspection.

**v2 ships Python only.** TS/JS in v2.1. Go/Rust/Java follow as graph-code-indexing's Gap B4
lands.

## Version awareness

**Critical design point**: the KB MUST reflect the exact version of each dependency in use by
the PR. This is what distinguishes "hallucinated" from "valid but for a different version".

- For Python: read `requirements.txt` / `pyproject.toml` / `Pipfile.lock` to get exact versions.
- For Node: read `package-lock.json` / `yarn.lock` / `pnpm-lock.yaml`.
- The actual packages in the repo's `node_modules` / virtualenv are the source of truth —
  if the PR's `package.json` change adds a new dependency but the lockfile hasn't been
  regenerated, the KB won't have the new package and the check errs on the side of "unresolved"
  (forwarded to LLM) rather than false-positive.

## Integration with the hallucination agent

Inside `agents/hallucination.md` Step 1 (Extract New Calls), AFTER parsing the diff but BEFORE
any LLM reasoning:

```
1. Invoke checkDiff({ diff, files, repoRoot }) via Bash:
      node lib/hallucination-ast/cli.js --diff-stdin > /tmp/halluc-pre.json
2. Read /tmp/halluc-pre.json.
3. For each finding with confidence 100 (guaranteed by the library):
      emit a FINDING_START block directly, do NOT reason about it further.
4. For each entry in unresolved[]:
      continue with the normal Step 2-8 LLM-based reasoning.
5. Track stats.wallMs — log to .soliton/state/runs/ for cost dashboard.
```

The agent's prompt should mention that findings already emitted by the AST pre-check are not
to be re-emitted.

## Corpus for validation

Khati 2026 published 200 Python samples (161 hallucinated, 39 clean). Use as a regression
corpus:

- `tests/fixtures/hallucination/khati-2026-python/` — checked in.
- Target: 100 % precision (zero FP) on the clean samples.
- Target: ≥ 87.6 % recall on the hallucinated samples (Khati baseline).

For TS/JS in v2.1, build a similar corpus from known AI-generated OSS PRs.

## Packaging

The library ships as a standalone Node package under `lib/hallucination-ast/`:

```
lib/hallucination-ast/
  package.json
  cli.js                  # entry: reads diff from stdin, emits JSON
  src/
    index.ts
    python/              # Python backend — subprocess + site-packages introspection
    typescript/          # TS/JS backend — .d.ts + TS compiler API
    knowledge.ts         # KB abstraction + caching
    similarity.ts        # Levenshtein for fix suggestions
  tests/
    fixtures/
```

Released on npm as `@soliton/hallucination-ast` (and vendored in soliton's GitHub Actions
workflow since Soliton the plugin itself is markdown-only).

## Cost model

Opus call cost for a HIGH-risk PR today: ~$0.15-0.30 for the hallucination agent alone.

With AST pre-check handling 80 % of cases:
- Python pre-check: ~50-200 ms, zero LLM tokens.
- Opus fallback on unresolved cases: ~$0.03-0.06.

Net expected saving: ~70-80 % of the hallucination-agent cost. On a 500-PR/day team that's
meaningful ($50-150/day saved).

## Non-goals (explicit)

- **No fuzzy matching beyond Levenshtein distance ≤ 2.** Anything fuzzier is LLM territory.
- **No cross-language resolution.** A Python file does not know about a TS module.
- **No dynamic import evaluation.** If the code is `importlib.import_module(dynamic_name)`,
  the library marks as unresolved and hands off.
- **No version downgrade/upgrade suggestions.** If an API exists in 2.x but not 1.x, we flag
  the hallucination; the developer decides whether to bump the version.
- **No auto-fix patches.** `suggestedFix` is the closest identifier string; applying it is
  out of scope for this library.

## Phase plan

- **Week 3 of pilot** (per `idea-stage/IDEA_REPORT.md` §9): ship Python backend + CLI +
  integration into `agents/hallucination.md`. Target: replace 80 % of Python hallucination-agent
  Opus calls.
- **Week 6-7 (post-pilot)**: TS/JS backend.
- **Month 3-4**: Go / Rust. Java / C++ follow graph-code-indexing's Gap B4 roadmap.

## Dependencies

- `tree-sitter` + `tree-sitter-python` / `tree-sitter-javascript` / `tree-sitter-typescript`
  (same tree-sitter as `graph-code-indexing` already vendors).
- `@babel/parser` (same).
- Runtime introspection subprocess (Python / Node) — a few hundred lines per language.

No new heavy deps. Shares infra with graph-code-indexing.

## References

- Khati et al. 2026 — "Detecting and Correcting Hallucinations in LLM-Generated Code via
  Deterministic AST Analysis" — arXiv [2601.19106](https://arxiv.org/abs/2601.19106).
- Spracklen et al. 2024/2025 — "Package Hallucinations in Code-Generating LLMs" —
  arXiv [2406.10279](https://arxiv.org/abs/2406.10279).
- Zhou et al. 2024 — "LLM Hallucinations in Practical Code Generation" —
  arXiv [2409.20550](https://arxiv.org/abs/2409.20550).
