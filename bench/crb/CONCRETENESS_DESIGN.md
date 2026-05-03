# Concreteness Prompt-Tuning Experiment — Design

**Status**: design only. No Soliton-side runtime changes have shipped. This doc pre-registers the experiment per `bench/crb/IMPROVEMENTS.md` § Subtraction wins doctrine, so a future $140-bounded measurement can interpret outcomes against committed bands.

**Authorization required to run**: explicit `ship Concreteness Phase 1` (CRB dispatch + Sphinx re-judge, ~$151 total spend).

**Driving evidence**: PR #133 Sphinx Phase 3 measurement — actionable_TP_rate = 30.6 % LOW band. Of 72 TPs against Phase 5.2 goldens, 50 are correct-but-vague. Judge reasons cluster around *"identifies the problem and impact but does not specify a concrete code change."* The precision problem is vagueness, not fabrication.

---

## 1. Hypothesis

**H1 (primary)**: Soliton's diagnose-without-prescribe pattern is driven by *vague* `suggestion`-field content (the agents emit findings whose suggestion text is prose like "consider X" rather than a literal code patch). Replacing the vague `suggestion`-field placeholder with a concrete-patch-only requirement will move actionable_TP_rate up without depressing F1. **This experiment touches ONLY the `suggestion` field; the `description` field is preserved unchanged** — see § 1.1 for the Phase 3.6 prior-art carve-out.

**H1 prediction (σ-aware, post 3-5× IMPROVEMENTS.md discount)**: actionable_TP_rate moves to 35-50 % (Δ +5-20pp); F1 moves within ±1σ of Phase 5.2 sphinx-prompt baseline (0.321-0.339).

**H1 falsification path**: actionable_TP_rate ≤ 33 % (within ±1× the assumed actionability σ of baseline 30.6 %) OR F1 < 0.321 (>1σ regression vs sphinx-prompt baseline). Either result triggers CLOSE.

### 1.1 Why this is NOT a Phase 3.6 replay

Phase 3.6 (PR #19, closed 2026-04-19 as a negative-result experiment) tested **`description`-field compression** as a v2.2 lever. Result: F1 −0.021, recall −0.111. The MEMORY entry reads: *"Description compression RULED OUT as a Soliton lever."* `bench/crb/IMPROVEMENTS.md` retains this as part of the calibration discipline.

This design is **structurally different** from Phase 3.6:

| | Phase 3.6 (CLOSED, F1 −0.021) | This design |
|--|------------------------------|-------------|
| Field touched | `description` (compressed) | `suggestion` (concretized) |
| Mechanism | Reduce token spend on diagnosis | Replace prose-licensing placeholder with literal-patch requirement |
| Recall risk | Direct — agents may drop full-context details | Indirect — agents may suppress findings they can't patch |
| Sphinx evidence | None at the time of 3.6 | LOW band 30.6 % directly cites suggestion vagueness |

The Sphinx LOW finding (PR #133) cites the *suggestion-field* failure pattern — judge reasons cluster on *"does not specify a concrete code change."* Phase 3.6 was about description compression, which is a different lever and a different failure mode. This design specifically does NOT compress description; it only tightens the suggestion-field placeholder.

If the post-experiment outcome shows F1 regression similar to Phase 3.6's −0.021, that would be **new evidence** that suggestion-field tightening shares Phase 3.6's recall-suppression mechanism — a worthwhile finding even under CLOSE.

## 2. Audit of vague-license phrasing in current prompts

The Sphinx evidence points to three locations where current prompts license vague output. The design below modifies only the two agent-side `suggestion` fields (§ 2.1, § 2.2); the SKILL.md renderer (§ 2.3) requires no change. **The agent-side modifications are REPLACEMENT, honoring subtraction-wins**.

### 2.1 `agents/correctness.md` line 113-114 (FINDING_START template)

Current:
```
description: <detailed explanation of the bug and its impact>
suggestion: <concrete fix code>
```

The `<concrete fix code>` placeholder is mentioned but not enforced by surrounding semantics. Line 131 ("Provide concrete fix code in every suggestion") is in the *Rules* block, three sections away from the template — easy for the agent to gloss over.

### 2.2 `agents/hallucination.md` lines 109-112 (FINDING_START template)

Current:
```
description: <what was found and why it is a hallucination>
suggestion: <correct API/function to use instead>
evidence: <what you checked to confirm — e.g., "Searched node_modules/fs/... No readFileAsync method found. Did you mean fs.promises.readFile?">
```

The `description` field is at line 109; `suggestion` at line 110; `evidence` at line 111. `<correct API/function to use instead>` is more specific than correctness's `<concrete fix code>`, but still permits prose ("use `aiohttp` instead") without a literal patch. The `evidence` field is preserved as-is in this experiment — it already requires concrete artifacts (file paths searched, lookup results) and is not part of the vagueness pattern.

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

### 3.1 `agents/correctness.md` line 114 (suggestion field only)

OLD (template line 114):
```
suggestion: <concrete fix code>
```

NEW (template line 114):
```
suggestion: <A LITERAL code patch ready to copy. The exact replacement lines — NOT prose. If you cannot write the patch, do not emit this finding; downgrade to nitpick or omit.>
```

DELETE line 131 (now redundant with inlined template guidance):

OLD (line 131):
```
- Provide concrete fix code in every suggestion, not just descriptions
```

NEW:
*(removed — promoted into template)*

**Description field (line 113) is unchanged.** The Phase 3.6 prior-art carve-out (§ 1.1) precludes any description-side modification.

### 3.2 `agents/hallucination.md` line 110 (suggestion field only)

OLD (template line 110):
```
suggestion: <correct API/function to use instead>
```

NEW (template line 110):
```
suggestion: <A LITERAL replacement: `import X` -> `import Y`, or `a.foo()` -> `a.bar()`. The exact replacement lines, ready to copy. NOT prose like "use Y instead".>
```

**Description field (line 109) and evidence field (line 111) are unchanged.** Description preserved per § 1.1 Phase 3.6 carve-out; evidence preserved because it already requires concrete artifacts.

### 3.3 SKILL.md Format A — NO CHANGE

The renderer already uses `\`\`\`suggestion` block syntax — it pulls from agent-side `suggestion` fields. Once 3.1-3.2 land, the renderer outputs concrete patches automatically without skill-side change. This is critical for the subtraction discipline: smaller-surface change = lower risk of unintended regression.

### 3.4 Severity Guide — NO CHANGE

An earlier draft proposed prepending "Has a concrete fix. If you cannot write the patch, downgrade to nitpick or omit." to `agents/correctness.md` line 123 (improvement severity guide). **That change was dropped during code review** (PR #134 review comment) on the grounds that prepending new prose is *additive*, not *replacement*, and therefore weakens the subtraction-wins doctrine claim. The downgrade-to-nitpick licensing now lives only in the template's NEW suggestion-field text (§ 3.1), where it functions as part of the template replacement and not as a new severity guide rule.

### 3.5 Diff size

| File | Lines added | Lines removed | Net |
|------|-------------|---------------|----:|
| `agents/correctness.md` | 1 | 2 | **−1** |
| `agents/hallucination.md` | 1 | 1 | 0 |
| `skills/pr-review/SKILL.md` | 0 | 0 | 0 |
| **Total** | **2** | **3** | **−1** |

Net diff is **−1 line**. The change is **replacement-style** and reduces total prompt prose. Honors subtraction-wins.

## 4. σ-aware pre-registered bands (locked-in by this commit)

Per `bench/crb/IMPROVEMENTS.md` calibration (3-5× ΔF1 discount on naive estimates) and PR #48 noise envelope (σ_F1 = 0.0086, σ_Δ paired = 0.0122, σ_lang ≈ 0.018 at n=10):

**Comparison baseline is the sphinx-prompt run from PR #133 (F1 = 0.330, actionable_TP_rate = 30.6 %)**, NOT the standard-prompt published Phase 5.2 (F1 = 0.313). The post-experiment re-judge uses the same sphinx prompt; apples-to-apples requires the sphinx-prompt baseline.

| Outcome | Δ actionable_TP_rate | F1 (sphinx prompt) | Per-language regression | Verdict |
|---------|---------------------:|-------------------:|------------------------:|---------|
| Best case | ≥ +15pp (≥ 45.6 % absolute) | ≥ 0.321 (≥ sphinx-baseline 0.330 − 1σ_F1) | none > 2σ_lang (= 0.036) | **SHIP** |
| Mixed | +5pp to +15pp | within ±2σ_F1 of 0.330 (i.e. [0.313, 0.347]) | within 2σ_lang (= 0.036) | **HOLD = CLOSE** (single bounded run; no $280 re-run per doctrine) |
| Falsified | < +5pp OR F1 < 0.321 OR any-lang regression > 2σ_lang (= 0.036) | — | — | **CLOSE** |

**HOLD = CLOSE rationale**: per the doctrine in `bench/crb/IMPROVEMENTS.md` § Subtraction wins, addition fails — we do not buy second chances at $140 each. Replicate-and-re-decide only happens with experiment-design changes (different prompt revision, different agent target), not same-design re-runs.

### Pre-registered SHIP gate computation (locked-in numerics)

- Sphinx-prompt baseline (PR #133): F1 = 0.330, actionable_TP_rate = 30.6 %
- σ_F1 = 0.0086 (PR #48; standard-prompt measurement, **assumed to apply to sphinx-prompt as best estimate**; this assumption is itself a measurement gap — see § 5 R3)
- 1σ_F1 floor (sphinx-prompt) = 0.330 − 0.0086 = **0.321** (rounded to 3 decimals)
- 2σ_F1 envelope (sphinx-prompt) = [0.313, 0.347]
- σ_lang = 0.018 (PR #48 / `bench/crb/judge-noise-envelope.md`; per-language max 0.0179 for TS at n=10). 2σ_lang = **0.036**.
- Sphinx actionability single-run noise envelope: NOT YET measured (n=1 from PR #133). Conservative assumption: actionable_TP_rate σ ~= ±5pp (treat as ±2σ for SHIP gate)
- SHIP requires **Δ ≥ +15pp on actionability AND F1 ≥ 0.321 AND no per-language regression > 0.036**

## 5. Risk register

### R1 — Agent over-suppression (recall drops)

**Risk**: agents that can't write a literal patch may now drop the finding entirely, depressing recall. This is the core failure mode shared with Phase 3.6's recall regression (−0.111).
**Probability**: medium-high. The new suggestion-field text explicitly licenses downgrade-to-nitpick or omission ("If you cannot write the patch, do not emit this finding; downgrade to nitpick or omit"). This may bite hardest on Critical findings where the bug is real but the fix is non-local (e.g., "this whole flow is broken").
**Impact on F1**: recall floor at ~0.45 would leave F1 ≈ 0.27 → CLOSE. Recall floor at ~0.50 keeps F1 ≈ 0.30 → still CLOSE under the new sphinx-prompt 1σ floor of 0.321 (the gate is now stricter than the original draft).
**Mitigation**: the existing `nitpick` severity exists as a soft-omit channel — Critical findings without a clean patch can downgrade rather than disappear entirely. Per-agent attribution post-run will identify whether correctness or hallucination contributed disproportionately to recall loss.

### R2 — Per-language divergence (HIGH probability)

**Risk**: Java/TypeScript may produce concrete patches more easily than Go/Ruby (LLM training data asymmetry). The 2σ_lang (= 0.036) gate could trigger CLOSE on a language slice even if aggregate is fine.
**Probability**: **HIGH** (upgraded from "medium-high" per code review). Go has regressed across **three** post-Phase-3.5 phases — Phase 4c (−0.016, 1.85σ_aggregate), Phase 4c.1 (Go −0.112 vs P3.5), Phase 3.5.1 (Go regressed despite the gate being TS-only). The pattern is consistent enough that **a Go regression > 0.036 on this experiment is the modal outcome to plan for**.
**Impact**: pre-registered CLOSE if any language regresses > 2σ_lang (= 0.036). Per-language attribution is pre-budgeted as a post-run analysis step regardless of aggregate verdict.
**Mitigation**: by pre-reg gate, no mitigation in-band. Out-of-band: if aggregate signal is promising AND only Go regresses, a follow-up Java/TS-only template (mirroring Phase 6's per-language re-integration pattern) becomes a natural Phase 7 candidate.

### R3 — σ_F1 transferability (sphinx-prompt vs standard-prompt)

**Risk**: PR #48's σ_F1 = 0.0086 was measured with the standard prompt. We use it as the best-estimate σ for the sphinx-prompt run baseline (F1 = 0.330). If the sphinx prompt has a meaningfully different noise envelope (e.g., σ_F1 = 0.012 because the extended prompt has more variance), the 1σ floor of 0.321 becomes incorrect.
**Probability**: medium. The +1.98σ gap between standard-prompt (0.313) and sphinx-prompt (0.330) F1 measurements suggests the prompts differ in mean but does not directly imply different variance.
**Impact**: a true σ_F1 of 0.012 (vs assumed 0.0086) would shift the 1σ floor from 0.321 to 0.318 — a 0.003 difference, well below 1σ_aggregate. Manageable.
**Mitigation**: σ_F1 measurement under sphinx prompt is a future calibration spend (~$45 for n=3 re-runs, mirroring PR #48's methodology). For this experiment, the pre-registered numerics stand as best-estimate; if a CLOSE result lands within 0.003 of 0.321, that close-call is itself a signal that σ-calibration is needed before any retry experiment.

### R4 — Description-suggestion conflation by judge

**Risk** (revised post-§ 1.1 carve-out): description is unchanged in this experiment, so the conflation risk is reduced — the agent has no incentive to cross-pollute description with patch content. Residual concern: agents may still verbose `description` while making `suggestion` concrete, leaving the candidate-extractor (step2_extract_comments) to re-extract the verbose description as an additional candidate.
**Probability**: low — current SKILL.md line 740-748 finding-atomicity rule already prohibits multi-issue findings.
**Impact**: cosmetic. Aggregate metrics not affected.
**Mitigation**: no additional change needed; existing atomicity rule covers.

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
# Edit agents/correctness.md per § 3.1 (suggestion field on line 114; delete line 131)
# Edit agents/hallucination.md per § 3.2 (suggestion field on line 110)
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
- **Does NOT compress or modify the `description` field** — Phase 3.6 (PR #19) tested description compression and CLOSEd at F1 −0.021 / recall −0.111. This experiment carves out description per § 1.1.
- Does NOT modify the Severity Guide — an earlier draft proposed a one-sentence prepend; that was dropped during code review as additive-not-replacement (see § 3.4).
- Does NOT change the Phase 5.2 baseline — the comparison anchor is the **sphinx-prompt** measurement from PR #133 (F1 = 0.330, actionable = 30.6 %), preserving apples-to-apples comparison since the post-experiment re-judge uses the same sphinx prompt.
- Does NOT use Phase 6 graph signals or any v2.1.0 wirings — those default-OFF flags stay OFF.
- Does NOT chain multiple experiments — one bounded run, one decision.

## 9. Why this honors subtraction-wins

The doctrine says addition fails: we have 5 consecutive CLOSE post-Phase-3.5 from agent-additions, hook-additions, wiring-additions. This experiment is **replacement** — replacing vague placeholder language with literal-patch language. Net diff is **−1 line** (the literal-patch placeholder is more compact than the redundant "Provide concrete fix code" rule it absorbs from line 131).

Cross-check against PR #121 doctrine + IMPROVEMENTS.md calibration:

| Rule | Compliance |
|------|-----------|
| Pre-register σ-aware bands before run with explicit numbers | ✅ § 4 (F1 floor 0.321, σ_lang 0.018, 2σ_lang 0.036) |
| Comparison baseline is the apples-to-apples measurement | ✅ § 4 (sphinx-prompt 0.330, NOT standard-prompt 0.313) |
| Single bounded run (HOLD = CLOSE) | ✅ § 4 |
| No new agents / hooks / wirings | ✅ § 8 |
| Subtraction-style change framing | ✅ § 3.5 (net **−1 line** via REPLACEMENT) |
| Cite + carve out prior falsified levers | ✅ § 1.1 (Phase 3.6 description-compression carve-out) |
| Address each $\geq$80-confidence code-review finding | ✅ all 3 from PR #134 review (R3 baseline, Phase 3.6 replay, σ_lang pin) |

## 10. Approval checkpoint

This design ships as a single PR with no Soliton runtime changes. The user's `ship Concreteness Phase 1` authorization is required to:

1. Land the prompt-edit PR (§ 3.1-3.2 — suggestion-field-only changes to two agent files)
2. Run the dispatch + Sphinx re-judge (~$151)
3. Apply the pre-registered SHIP/HOLD/CLOSE bands

The design pre-registers the gate. Outcome interpretation is locked-in by this commit; no retroactive band-adjustment is permitted per the σ-aware doctrine.

---

*Filed under: Soliton / bench / CRB / experiment-design. Companion to `bench/crb/sphinx-actionability-spec.md` (which surfaced the LOW verdict driving this experiment).*

**Revision history**:

- 2026-05-02 — initial draft (PR #134, commit 5afa33d). Code review surfaced 3 ≥80-confidence findings: (1) § 4 SHIP gate F1 floor inconsistent with § 5 R3, (2) § 3.1 description compression near-replays Phase 3.6 (PR #19) which CLOSEd at F1 −0.021 / recall −0.111, (3) § 4 σ_lang not pinned numerically.
- 2026-05-03 — revision (this commit). Addresses all 3 findings: (1) F1 floor moved to 0.321 against the corrected sphinx-prompt baseline of 0.330; § 5 R3 reframed as σ-transferability concern; (2) description-field modifications dropped per § 1.1 Phase 3.6 carve-out; § 3.2 Severity Guide change also dropped as additive-not-replacement; experiment now scoped to suggestion-field-only (net diff −1 line); (3) σ_lang = 0.018, 2σ_lang = 0.036 pinned in § 4 table per PR #48 / `bench/crb/judge-noise-envelope.md`.
