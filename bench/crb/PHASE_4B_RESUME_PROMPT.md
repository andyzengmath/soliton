# Phase 4b resume prompt — for next-session start

Drop the **fenced block below** verbatim into a fresh Claude Code session to resume Phase 4b implementation work (`lib/hallucination-ast/` Python package per `lib/hallucination-ast.md`'s spec).

The prompt is self-contained: a fresh session won't have prior conversation context but will auto-load `MEMORY.md`, which indexes Phase 2/3/3.5/3.6/3.7/4a status. The prompt below points the next-session-me at the exact files to read and the deliverables to ship.

---

```text
Resume session: Soliton Phase 4b — implement lib/hallucination-ast/ Python package.

Current state (verify with git log --oneline -5):
- Repo: andyzengmath/soliton. main HEAD around 4222bea (PR #24 merge).
- 0 open PRs.
- 4 PRs merged this/last session: #15 (leaderboard doc), #16 (Phase 3 baseline),
  #17 (IMPROVEMENTS plan), #21 (Phase 3.5 — current published F1=0.277),
  #22 (Phase 4 design doc), #23 (IMPROVEMENTS calibration notice),
  #24 (Phase 4a L5 cross-file-retrieval skill).
- 2 PRs closed as documented negative-result experiments: #19 (v2.2 description
  compression), #20 (v2.3 synthesizer dedup widening). DO NOT re-attempt those
  levers — both falsified.

Required reading at session start, IN ORDER (auto-memory loads MEMORY.md first;
those notes already index everything below):
1. MEMORY.md — full context: project, role, Phase 2/3/3.5 results, Phase 3.6/3.7
   negatives, Phase 4a status, Phase 4 design.
2. bench/crb/PHASE_4_DESIGN.md — the overall Phase 4 plan including 4b's place in
   it. Especially §"Component B" and §"Rollout plan".
3. lib/hallucination-ast.md — the Phase 4b spec to implement. ~80 lines, includes
   the design diagram and TypeScript interface stubs (which are illustrative;
   actual implementation is Python).
4. skills/pr-review/cross-file-retrieval.md (Phase 4a, just shipped) — your
   external-symbol handoff target. The hallucination agent currently emits
   "source: NOT_FOUND_IN_TREE" and DEFERS to your AST checker; closing that gap
   is the load-bearing reason Phase 4b ships before Phase 4c CRB validation.
5. agents/hallucination.md (current state on main) — where lib/hallucination-ast
   plugs in as a pre-check before the Opus reasoning step.

Goal for this session: ship Phase 4b.

Concrete deliverables (in priority order):

A. lib/hallucination-ast/ Python package (new):
   - pyproject.toml with deps: tree-sitter, tree-sitter-python (day-one ONLY;
     other languages deferred to 4b.x patches)
   - lib/hallucination_ast/__init__.py
   - lib/hallucination_ast/extract.py — diff parser → AstExtractedReference[]
     (per the spec's TypeScript stub)
   - lib/hallucination_ast/resolve.py — symbol → installed-package introspection
     (start with site-packages; node_modules/go.sum/maven deferred)
   - lib/hallucination_ast/check.py — four finding rules: identifier_not_found,
     signature_mismatch_arity, signature_mismatch_keyword, deprecated_identifier
   - lib/hallucination_ast/cli.py — `python -m hallucination_ast --diff <path>`
     entry point that emits JSON findings (matches the format in
     skills/pr-review/cross-file-retrieval.md's NOT_FOUND_IN_TREE handoff)

B. Validation suite — SHIPPING GATE: before integrating into Soliton, reproduce
   Khati 2026's published numbers (100% precision, 87.6% recall, F1=0.934) on
   their test set (161 hallucinated + 39 clean Python samples). The paper is
   arXiv 2601.19106. Their test set is published — fetch it.
   - lib/hallucination_ast/tests/test_khati_corpus.py
   - If our reproduction lands < 95% precision OR < 80% recall on Khati's
     samples, STOP — implementation is broken, do not integrate.

C. Integration into agents/hallucination.md (only after B passes): add a
   §"Step 2.5: Run hallucination-AST pre-check" that shells out to the new CLI,
   ingests the JSON findings, emits them at confidence 100 (bypassing the Opus
   reasoning step for clean hits), then proceeds to Step 3 with the remaining
   ambiguous cases.

D. Test fixtures:
   - tests/fixtures/hallucinated-import/ (a diff that imports requests.gett —
     typo for .get; should be caught at confidence 100)
   - tests/fixtures/signature-mismatch/ (a call to requests.get with wrong
     kwargs)
   - Update tests/run-fixtures.md with both.

Operational rules (carry over from prior sessions; auto-mode is your default):
- Confirm before any gh pr merge on PRs you open.
- Confirm before any spend > $5 (Phase 4b should be ~$0 — pure Python work, no
  LLM calls in the implementation itself).
- DO NOT run any CRB pipeline this session — Phase 4c only triggers after both
  4a (already shipped) AND 4b (this session) are dogfood-validated.
- Use the same git workflow as prior sessions: branch from main, commit
  thematically, open PR with detailed body + test plan, do not merge without
  explicit user confirmation.
- gh pr edit may be blocked on this repo by a GitHub Projects-classic API bug —
  if you hit it, post a comment on the PR with corrections rather than retrying.

What NOT to do (memory-backed):
- Do NOT propose new prompt-level F1 levers (description compression, dedup
  widening, etc.) — Phase 3.6 and 3.7 falsified two; ceiling is around 0.277.
- Do NOT trust IMPROVEMENTS.md ΔF1 estimates as written — every projection has
  the calibration-notice 3-5x discount applied.
- Do NOT run the CRB pipeline. Phase 4c is triggered by user, not autonomously.
- Do NOT add the other 4 languages (Go/TS/Java/Ruby) to hallucination-AST in
  this session unless the Python implementation finishes early. They are
  Phase 4b.1 / 4b.2 follow-ups.
- Do NOT skip the Khati 2026 validation gate. If our reproduction misses, the
  whole investment is moot.

Budget for Phase 4b implementation: pure Python work, no LLM spend expected.
Validation gate may run a tiny test corpus through the AST checker — local
Python execution, free. Phase 4c (when triggered later) will be the next ~$100
spend.

Phase 4 ship criteria (pre-registered in PHASE_4_DESIGN.md, applies at Phase 4c,
not this session): F1 >= 0.32 AND recall >= 0.64 AND no per-language regression
> 0.02 vs Phase 3.5. Holding-pattern outcome (F1 in 0.29-0.31 range) means
ship whichever of (4a alone, 4b alone) passes individually. Negative outcome
(F1 < 0.29) closes Phase 4 and we revisit deeper structural work (I19 sandbox).

What I want in writing back early in this session (after reading the 5 docs
above):
- Your scaffolding plan for lib/hallucination-ast/ — file layout, deps,
  Python version target. Should match the spec's TypeScript-stub shape, just
  in Python.
- Where you'll fetch the Khati 2026 test corpus from (paper ref or repo URL).
- Your read on whether the existing spec at lib/hallucination-ast.md needs
  any clarification before you start writing code (e.g., is anything
  ambiguous, contradictory, or missing?).
- Confirm the dogfood + integration order: validate against Khati FIRST, then
  integrate into hallucination.md, then dogfood on Soliton's own PRs, THEN
  user decides whether to trigger Phase 4c CRB run.

If anything is ambiguous in this prompt, ask before diverging.

Do not re-run /research-pipeline, /code-review, or /pr-review against Phase 3
historical PRs. Those are evaluated.
```
