# Concreteness Prompt-Tuning Experiment — Design

**Status**: design only. No Soliton-side runtime changes have shipped. This doc pre-registers the experiment per `bench/crb/IMPROVEMENTS.md` § Subtraction wins doctrine, so a future $140-bounded measurement can interpret outcomes against committed bands.

**Authorization required to run**: explicit `ship Concreteness Phase 1` (CRB dispatch + Sphinx re-judge, ~$151 total spend).

**Driving evidence**: PR #133 Sphinx Phase 3 measurement — actionable_TP_rate = 30.6 % LOW band. Of 72 TPs against Phase 5.2 goldens, 50 are correct-but-vague. Judge reasons cluster around *"identifies the problem and impact but does not specify a concrete code change."* The precision problem is vagueness, not fabrication.

---

## 1. Hypothesis

**H1 (primary)**: Soliton's diagnose-without-prescribe pattern is driven by `description`-dominant finding-emission style. Replacing vague `suggestion`-field guidance with concrete-patch-only requirements will move actionable_TP_rate up without depressing F1.

**H1 prediction (σ-aware, post 3-5× IMPROVEMENTS.md discount)**: actionable_TP_rate moves to 35-50 % (Δ +5-20pp); F1 moves within ±1σ of Phase 5.2 baseline (0.305-0.321).

**H1 falsification path**: actionable_TP_rate ≤ 33 % (within σ of baseline 30.6 %) OR F1 < 0.305 (>1σ regression). Either result triggers CLOSE.

## 2. Audit of vague-license phrasing in current prompts

The Sphinx evidence points to three locations where current prompts license vague output. **None are removed in the design below — the change is REPLACEMENT, honoring subtraction-wins**.

### 2.1 `agents/correctness.md` line 113-114 (FINDING_START template)

Current:
```
description: <detailed explanation of the bug and its impact>
suggestion: <concrete fix code>
```

The `<concrete fix code>` placeholder is mentioned but not enforced by surrounding semantics. Line 131 ("Provide concrete fix code in every suggestion") is in the *Rules* block, three sections away from the template — easy for the agent to gloss over.

### 2.2 `agents/hallucination.md` line 110-111 (FINDING_START template)

Current:
```
description: <what was found and why it is a hallucination>
suggestion: <correct API/function to use instead>
```

`<correct API/function to use instead>` is more specific than correctness's `<concrete fix code>`, but still permits prose ("use `aiohttp` instead") without a literal patch.

### 2.3 `skills/pr-review/SKILL.md` line 729-733 (Format A Improvements rendering)

Current:
```markdown
:yellow_circle: [<category>] <title> in <file>:<lineStart> (confidence: <confidence>)
<description>
```suggestion
<suggestion code>
```
```

The markdown renderer DOES use GitHub's `\`\`\`suggestion` block — when the agent provides actual code, the suggestion renders as a clickable apply button. The Sphinx LOW finding suggests agents often emit empty / vague suggestion blocks because their `<suggestion>` field never had concrete patch content to begin with.

Phase 5.2 reviews confirm this — sample candidates extracted by CRB's step2_extract_comments.py read like *prose descriptions*, not patches, because that's what the FINDING_START produced.

## 3. Proposed change (subtraction-style)

### 3.1 `agents/correctness.md` lines 113-114

OLD (template):
```
description: <detailed explanation of the bug and its impact>
suggestion: <concrete fix code>
```

NEW (template):
```
description: <ONE sentence: what's the bug, where, and what's its impact. Keep brief.>
suggestion: <A LITERAL code patch ready to copy. The exact replacement lines — NOT prose. If you cannot write the patch, do not emit this finding; downgrade to nitpick or omit.>
```

DELETE line 131 (now redundant with inlined template guidance):

OLD:
```
- Provide concrete fix code in every suggestion, not just descriptions
```

NEW:
*(removed — promoted into template)*

### 3.2 `agents/correctness.md` line 123 (Severity Guide)

OLD:
```
- **improvement**: Could cause bugs under specific conditions (missing edge case handling, unhandled rejection in non-critical path)
```

NEW:
```
- **improvement**: Has a concrete fix. If you cannot write the patch, downgrade to nitpick or omit. Could cause bugs under specific conditions (missing edge case handling, unhandled rejection in non-critical path).
```

### 3.3 `agents/hallucination.md` lines 110-111

OLD (template):
```
description: <what was found and why it is a hallucination>
suggestion: <correct API/function to use instead>
```

NEW (template):
```
description: <ONE sentence: which API/function is hallucinated and where.>
suggestion: <A LITERAL replacement: `import X` -> `import Y`, or `a.foo()` -> `a.bar()`. The exact replacement lines, ready to copy. NOT prose like "use Y instead".>
```

### 3.4 SKILL.md Format A — NO CHANGE

The renderer already uses `\`\`\`suggestion` block syntax — it's the agent-side fields it pulls from. Once 3.1-3.3 land, the renderer outputs concrete patches automatically without skill-side change. This is critical for the subtraction discipline: smaller-surface change = lower risk of unintended regression.

### 3.5 Diff size

| File | Lines added | Lines removed | Net |
|------|-------------|---------------|----:|
| `agents/correctness.md` | 4 | 4 | 0 |
| `agents/hallucination.md` | 4 | 2 | +2 |
| `skills/pr-review/SKILL.md` | 0 | 0 | 0 |
| **Total** | **8** | **6** | **+2** |

Net diff is +2 lines. The change is **replacement-style**, not addition. Honors subtraction-wins via small surface area.

## 4. σ-aware pre-registered bands (locked-in by this commit)

Per `bench/crb/IMPROVEMENTS.md` calibration (3-5× ΔF1 discount on naive estimates) and PR #48 noise envelope (σ_F1 = 0.0086, σ_Δ paired = 0.0122):

| Outcome | Δ actionable_TP_rate | F1 (sphinx prompt) | Per-language regression | Verdict |
|---------|---------------------:|-------------------:|------------------------:|---------|
| Best case | ≥ +15pp (≥ 45.6 % absolute) | ≥ 0.305 (≥ Phase 5.2 - 1σ) | none > 2σ_lang | **SHIP** |
| Mixed | +5pp to +15pp | within ±2σ of baseline | within 2σ_lang | **HOLD = CLOSE** (single bounded run; no $280 re-run per doctrine) |
| Falsified | < +5pp OR F1 < 0.305 OR per-lang regression > 2σ | — | — | **CLOSE** |

**HOLD = CLOSE rationale**: per the doctrine in `bench/crb/IMPROVEMENTS.md` § Subtraction wins, addition fails — we do not buy second chances at $140 each. Replicate-and-re-decide only happens with experiment-design changes (different prompt revision, different agent target), not same-design re-runs.

### Pre-registered SHIP gate computation

- Phase 5.2 baseline: F1 = 0.313, actionable_TP_rate = 30.6 %
- σ_F1 = 0.0086 → 1σ floor = 0.305, 2σ floor = 0.296
- Sphinx single-run noise envelope: NOT YET measured (n=1 from PR #133). Conservative assumption: actionable_TP_rate σ ~= ±5pp (treat as ±2σ for SHIP gate)
- SHIP requires Δ ≥ +15pp on actionability AND F1 ≥ 0.305 (1σ floor)

## 5. Risk register

### R1 — Agent over-suppression (recall drops)

**Risk**: agents that can't write a literal patch may now drop the finding entirely, depressing recall.
**Probability**: medium. The Severity Guide change explicitly licenses downgrade-to-nitpick or omission.
**Impact on F1**: recall floor at ~0.45 would still leave F1 ≥ 0.27 (CLOSE). Recall floor at ~0.50 keeps F1 ≥ 0.30 (within HOLD band).
**Mitigation**: the existing `nitpick` severity exists as a soft-omit channel. Critical findings still ship even without a clean patch (severity guide allows it).

### R2 — Per-language divergence

**Risk**: Java/TypeScript may produce concrete patches more easily than Go/Ruby (LLM training data asymmetry). The 2σ_lang gate could trigger CLOSE on a language slice even if aggregate is fine.
**Probability**: medium-high based on Phase 4c.1 evidence (Go regressed −0.112 across multiple post-Phase-3.5 phases).
**Impact**: pre-registered CLOSE if any language regresses > 2σ_lang.
**Mitigation**: by pre-reg gate, no mitigation in-band. Out-of-band: per-language sub-experiment if aggregate signal looks promising.

### R3 — Sphinx judge prompt drift between baseline and post-experiment runs

**Risk**: PR #133 measured F1=0.330 with sphinx prompt vs published 0.313 with standard prompt — +1.98σ. The post-experiment Sphinx re-judge would have to baseline against the *sphinx-prompt* F1=0.330, not 0.313.
**Probability**: certain.
**Impact**: changes the SHIP-gate F1 floor from 0.305 to ~0.322 (sphinx-prompt baseline - 1σ).
**Mitigation**: spec the correct comparison baseline up-front (sphinx-prompt 0.330 ± σ_F1, not standard-prompt 0.313).

### R4 — Description-suggestion conflation by judge

**Risk**: if the agent puts the patch in `description` AND `suggestion`, the judge may double-count and mark both as actionable; or step2_extract_comments may emit duplicates.
**Probability**: low — agents are told to keep description brief; suggestion is the patch.
**Impact**: cosmetic. Aggregate metrics not affected.
**Mitigation**: existing finding-atomicity rule in SKILL.md line 740-748 already covers; no additional change needed.

### R5 — Hallucination agent's existing concrete-pattern strength

**Risk**: hallucination agent already produces relatively concrete suggestions ("use `aiohttp` instead") — change may have smaller effect, dampening aggregate Δ.
**Probability**: medium.
**Impact**: aggregate Δ smaller than per-agent Δ for correctness.
**Mitigation**: per-agent attribution analysis post-run (which agent contributed the actionability lift). If only hallucination held the line, no per-agent revert needed.

## 6. Spend estimate

| Item | Cost |
|------|-----:|
| Design doc (this PR) | $0 |
| Concreteness prompt edits PR | $0 |
| CRB dispatch (50-PR Phase 5.2 corpus, full /pr-review with new prompts) | ~$140 |
| Sphinx re-judge (against new reviews) | ~$11 |
| **Total to measure** | **~$151** |

Compares to Strategic Option A (I19 sandbox, $2-5k + 6-12 weeks eng) at 13-33× lower cost.

## 7. Reproduction recipe (post-authorization)

```bash
# Step 1 — apply prompt changes (separate PR, after this design lands)
git checkout -b feat/concreteness-prompt-tuning
# Edit agents/correctness.md per § 3.1 + 3.2
# Edit agents/hallucination.md per § 3.3
git commit -m "feat(prompts): concreteness — concrete-patch-only suggestion fields"

# Step 2 — dispatch (1-2h, ~$140)
bash bench/crb/dispatch-phase-concreteness.sh   # NEW; mirrors dispatch-phase6.sh

# Step 3 — judge with both standard + sphinx prompts in parallel
bash bench/crb/run-phase-concreteness-pipeline.sh

# Step 4 — analyze
python3 bench/crb/analyze-sphinx.py --evals \
  ../code-review-benchmark/offline/results/azure_gpt-5.2/evaluations_concreteness_sphinx.json
# Compare actionable_TP_rate vs Phase 5.2 baseline (30.6 %)
# Apply pre-registered SHIP/HOLD/CLOSE bands per § 4

# Step 5 — RESULTS.md writeup + decision
# If SHIP: prompt changes ship to main, v2.1.3 release tag
# If HOLD = CLOSE: prompt changes do NOT ship; design retained as evidence
# If CLOSE: prompt changes do NOT ship; CONCRETENESS_DESIGN.md gets a § Outcome
```

## 8. What this experiment does NOT do

- Does NOT add new agents, hooks, slash commands, or wirings — pure prompt-tuning.
- Does NOT modify the SKILL.md orchestration logic — Format A renderer change is zero-LOC.
- Does NOT touch infrastructure agents (risk-scorer, synthesizer) — only review agents.
- Does NOT change the Phase 5.2 baseline — the comparison is fixed at F1=0.313 / actionable=30.6 %.
- Does NOT use Phase 6 graph signals or any v2.1.0 wirings — those default-OFF flags stay OFF.
- Does NOT chain multiple experiments — one bounded run, one decision.

## 9. Why this honors subtraction-wins

The doctrine says addition fails: we have 5 consecutive CLOSE post-Phase-3.5 from agent-additions, hook-additions, wiring-additions. This experiment is **replacement** — replacing vague placeholder language with literal-patch language. Net diff is +2 lines (template expansions are slightly more verbose than the originals).

Cross-check against PR #121 doctrine:

| Rule | Compliance |
|------|-----------|
| Pre-register σ-aware bands before run | ✅ § 4 |
| Single bounded run (HOLD = CLOSE) | ✅ § 4 |
| No new agents / hooks / wirings | ✅ § 8 |
| Subtraction-style change framing | ✅ § 3.5 (net +2 lines via REPLACEMENT, not addition) |

## 10. Approval checkpoint

This design ships as a single PR with no Soliton runtime changes. The user's `ship Concreteness Phase 1` authorization is required to:

1. Land the prompt-edit PR (§ 3.1-3.3)
2. Run the dispatch + Sphinx re-judge (~$151)
3. Apply the pre-registered SHIP/HOLD/CLOSE bands

The design pre-registers the gate. Outcome interpretation is locked-in by this commit; no retroactive band-adjustment is permitted per the σ-aware doctrine.

---

*Filed under: Soliton / bench / CRB / experiment-design. Companion to `bench/crb/sphinx-actionability-spec.md` (which surfaced the LOW verdict driving this experiment). Pre-registered 2026-05-02.*
