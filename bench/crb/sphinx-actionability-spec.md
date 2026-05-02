# Sphinx Actionability Metric — Spec for CRB Judge Addendum

**Purpose**: spec a secondary CRB metric that measures *actionability* (would a developer actually change code in response to this finding?) alongside the standard match-F1 (does this finding match a golden comment?). Closes the A4 §S2 finding from the 2026-05-02 4-doc cross-walk.

**Status**: scope-before-build proposal. The metric integration lives in the sibling `../code-review-benchmark/offline/` harness (specifically the `step3_judge_comments.py` pipeline), not in Soliton itself. This doc specifies the prompt addendum + result schema so the integration can happen quickly when authorized.

**Cost to integrate**: $0 code (judge-prompt edit only) + ~$15 to re-judge existing Phase 5.2 reviews against the new metric (no re-dispatch of Soliton; same review markdown, second pass through judge with extended prompt).

---

## Why this metric matters

The Sphinx paper (Joshi et al., arXiv 2511.XXXX, cited in `Logical_inference/docs/strategy/2026-05-01-A4-literature-delta.md` § S2) defines **actionability** as: did this comment cause the developer to actually change code? They measure it by checking whether subsequent commits in the PR addressed the comment's concern.

For Soliton's CRB scoring, **standard F1 conflates two failure modes**:

1. **Wrong**: the finding identifies an issue that doesn't exist, or has the wrong file/line/category.
2. **Correct but not actionable**: the finding is technically valid but too vague / too low-priority / too style-driven for a developer to take action on.

Both currently count as FPs in the Phase 5.2 F1=0.313 number. The Sphinx metric lets us **distinguish them** — a finding can be "judged correct against goldens" AND "non-actionable" simultaneously.

This matters because:

- Soliton's three-pass dogfood pattern (PRs #107/#108/#110/#122/#123/#125/#126 in this session) consistently catches *actionable* bugs (config plumbing dead-ends, fabricated numbers, structural fragility). The CRB number doesn't currently distinguish these from style-nit FPs.
- Procurement audiences asking "does Soliton catch real bugs?" want the actionability slice. The cost-normalised F1 narrative would be substantially stronger paired with an actionability split.
- If actionability scoring shows Soliton's precision problem is "correct but vague" (as opposed to "wrong"), the fix is prompt-tuning toward concrete suggestions — a known-cheap lever. If it's mostly "wrong", architectural work (Phase 6 / I19) is needed.

---

## Proposed integration

### 1. Judge-prompt addendum

The CRB step3 judge currently asks (paraphrased): *"Does this Soliton candidate match this golden comment?"* with an output schema of `{ matched: bool, confidence: int }`.

The Sphinx addendum extends the prompt to ask:

```
Does this Soliton candidate identify an issue the developer would
ACTUALLY change code for? Even if the candidate matches a golden, mark
actionability=false when:

- The finding says "consider X" without naming a concrete change
- The finding is style-only (formatting, naming, ordering) without
  citing a specific style-guide rule the codebase enforces
- The suggestion would not pass code review (overly broad, speculative)
- The fix would not change observable behavior or maintainability
- The finding is correct but redundant with documentation already
  visible at the change site

Mark actionability=true when:

- The finding names a specific code change (replace X with Y)
- The change has a concrete impact (fixes a bug, prevents a regression,
  improves a measurable quality)
- A typical reviewer would request this change before merge
```

Output schema extension:

```json
{
  "matched": true,
  "confidence": 95,
  "actionability": "actionable" | "non_actionable" | "uncertain",
  "actionability_reason": "<1-sentence explanation>"
}
```

### 2. Aggregation

Per-corpus actionability score:

```
actionable_TP_rate = (count of TPs with actionability=actionable) / TP
non_actionable_TP_rate = (count of TPs with actionability=non_actionable) / TP
```

Per-language: same breakdown sliced by language.

### 3. Reporting in `bench/crb/RESULTS.md`

For each phase that re-runs the actionability addendum, add a row to the summary table:

| Phase | F1 | TP-actionable | TP-non-actionable | TP-uncertain |
|-------|----|---------------|-------------------|---------------|
| Phase 5.2 (re-scored) | 0.313 | TBD | TBD | TBD |

If `TP-actionable / TP > 0.7`, that's evidence Soliton's findings are predominantly real bugs; the precision problem is in FP-volume not FP-quality. If `< 0.5`, that's a signal to invest in concreteness-prompt-tuning.

---

## Pre-registration (σ-aware per A4 doctrine)

Sphinx actionability is a **secondary metric**, not a SHIP/HOLD/CLOSE decision lever. We do NOT pre-register a F1-style ship band — the metric is informational. But to prevent retroactive cherry-picking, pre-register the interpretation:

| `TP-actionable / TP` | Interpretation | Action |
|----------------------|----------------|--------|
| ≥ 0.70 | High actionability — Soliton's TPs are mostly real bugs | Cite alongside F1 in publishable narrative |
| 0.50-0.69 | Mixed — some style/vague findings dilute the TP set | Concrete-prompt experiment (~$140 CRB run) becomes a candidate |
| < 0.50 | Low actionability — TPs are predominantly non-bug findings | Re-evaluate whether F1=0.313 is a meaningful number; may need stronger judge filter |

These bands are pre-registered as of this doc's commit. Subsequent measurement results will be cited verbatim.

---

## Why this is sibling-repo work, not Soliton-side

The CRB scoring pipeline lives in `../code-review-benchmark/offline/code_review_benchmark/step3_judge_comments.py`. Soliton's repo can only:

1. **Spec the metric** (this doc).
2. **Provide the prompt-addendum text** (above).
3. **Provide the integration touch-points** (which step in the pipeline gets the addendum + schema field).

The actual implementation requires:

- Editing `step3_judge_comments.py` to extend the prompt with the addendum
- Extending the result-schema validation to include `actionability` + `actionability_reason`
- Re-running step3 against existing reviews (no Soliton re-dispatch needed; same review markdown → second judge pass)
- Plumbing `actionability` aggregation into `step4_summary.py` (or the equivalent results aggregator)

These edits are mechanical and low-risk (~half-day's work on the harness). No Soliton plugin changes.

---

## Cost / spend authorization

| Phase | Cost | Authorization |
|-------|------|---------------|
| 1 — write this spec | $0 | autonomous (this PR) |
| 2 — sibling-repo harness edits | $0 (code only) | sibling-repo PR; user / co-maintainer review |
| 3 — re-judge Phase 5.2 reviews against actionability prompt | ~$15 | needs explicit go-ahead per the bounded-spend doctrine |
| 4 — write up actionability split in `bench/crb/RESULTS.md` | $0 | autonomous after Phase 3 results land |

Phases 2-4 sequence after this spec lands. Phase 3 is the only spend gate ($15) and is strictly bounded.

---

## Non-goals

- **Not a primary metric replacement**: actionability supplements F1, doesn't replace it. The Martian leaderboard's primary metric remains match-F1 against goldens.
- **Not a ship/hold/close decision input**: actionability is informational. SHIP/HOLD/CLOSE for Phase 6+ is still based on aggregate F1 + per-language slice.
- **Not a behavioral change to Soliton**: the review pipeline is unchanged. This metric is purely judge-side instrumentation.
- **Not a Sphinx paper re-implementation**: we're applying the *concept* (actionability scoring) via a judge-prompt addendum, not replicating their full methodology (which uses post-hoc commit analysis to verify whether comments caused changes).

---

## Strategic context

The 2026-05-02 4-agent cross-walk identified this as A4's highest-value-per-cost actionable item: $15 to re-score existing reviews would yield a procurement-grade actionability number that pairs with the cost-normalised F1 / first-mover claim narrative. Combined with PR #124's self-validation evidence catalog, it gives buyers three independent quality signals:

1. **F1=0.313** on Martian CRB phase5_2-reviews/ corpus (raw match accuracy)
2. **F1/$ = 2.14 real-world / 0.855 CRB** (cost-normalised efficiency, first-mover claim)
3. **Actionability split** of the F1 numerator (real-bug-rate, this proposal)

Buyers asking "does it catch real bugs cheaply?" can cite all three. This proposal shows how to add the third without re-running Soliton's expensive dispatch.

---

*Filed under: Soliton / bench / CRB / metric-extensions. Companion spec to `bench/crb/cost-normalised-f1.md` and `bench/crb/PHASE_6_DESIGN.md`. To execute: PR against the sibling `code-review-benchmark` repo + re-judge spend authorization.*
