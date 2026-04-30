# Phase 5.3 — combined v2.1.0 wirings CRB result

*Result writeup, 2026-04-29 evening — A6 closed.*

**TL;DR.** A 50-PR CRB run with all four v2.1.0 newly-wired agents active produces **F1 = 0.268** (P=0.183, R=0.500), a **−0.045 regression vs Phase 5.2's published 0.313**. Per the σ-aware pre-registration in `idea-stage/POST_V2_FOLLOWUPS.md` §A6 (σ_F1=0.0086, 2σ_Δ paired = 0.024), the regression is well outside the noise band and triggers the **❌ CLOSE** verdict. Out-of-pocket: ~$140 (≈$125 dispatch + $15 judge). Critical-severity recall stays 0.889 (unchanged); the regression is in High/Medium/Low recall and across-the-board precision.

## Pre-registration (POST_V2_FOLLOWUPS §A6)

- ✅ **Ship:** F1 ≥ 0.337 AND recall ≥ 0.50 AND no per-language reg > 0.036
- ⚠️ **Hold:** 0.325 ≤ F1 < 0.337
- ❌ **Close:** F1 < 0.313

Phase 5.3's F1 = 0.268 is below the Close floor → **CLOSE**.

## What this run actually measured

`.claude/soliton.local.md` enabled all four wirings simultaneously:

| Wiring | PR | Default | Triggered when… |
|---|---|---|---|
| `realist-check` Step 5.5 | #50 | OFF (set ON in this run) | ≥ 1 CRITICAL or high-confidence IMPROVEMENT in synthesis |
| `silent-failure` Step 4.1 | #51 | ON | diff contains try/catch/Promise/optional-chaining patterns |
| `comment-accuracy` Step 4.1 | #51 | ON | diff modifies comment-marker lines |
| `cross-file-impact` graphSignals consumption | #61 | OFF (gated on `graph.enabled` — set ON in this run) | `graphSignals.dependencyBreaks[]` present and non-empty |

Same 50-PR `phase3-dispatch-list.txt` corpus as every prior phase. Same `run-poc-review.sh` driver. Same Azure GPT-5.2 judge pipeline.

## Headline numbers

| Metric | Phase 3.5 | Phase 5 | Phase 5.2 | **Phase 5.3** | Δ vs P5.2 |
|---|---:|---:|---:|---:|---:|
| F1 | 0.277 | 0.300 | 0.313 | **0.268** | **−0.045** |
| Precision | 0.183 | 0.210 | 0.224 | **0.183** | −0.041 |
| Recall | 0.566 | 0.522 | 0.522 | **0.500** | −0.022 |
| TP | 77 | 71 | 71 | 68 | −3 |
| FP | 343 | 267 | 246 | **303** | +57 |
| FN | 59 | 65 | 65 | 68 | +3 |
| Mean candidates / PR | 8.4 | 6.9 | 6.6 | **7.7** | +1.1 |

The candidate-extractor surfaced **+1.1 more candidates per PR** vs Phase 5.2 (mean 7.7 vs 6.6). The new candidates are mostly precision-killers — TP went up by only 0 (68 vs 71 actually slightly down), while FP jumped +57. The four wirings collectively emit more findings, but the marginal findings are mostly noise from the CRB judge's perspective.

## Per-language breakdown

| Lang | n | P3.5 F1 | P5.2 F1 | **P5.3 F1** | Δ vs P5.2 | Note |
|---|--:|--:|--:|--:|--:|---|
| **TS** | 10 | 0.266 | 0.342 | **0.336** | −0.006 | Held; graph-signal consumption may be helping (P5.3 TS=0.336 vs the per-lang noise floor 0.018, 0.336 holds). |
| Python | 10 | 0.237 | 0.311 | 0.216 | −0.095 | Big regression. Python diffs trigger silent-failure (try/except common) + cross-file-impact (graph has Python). |
| Ruby | 10 | 0.291 | 0.312 | 0.252 | −0.060 | Comment-accuracy frequent-fires on `#`-prefixed comment edits. |
| Go | 10 | 0.326 | 0.320 | 0.282 | −0.038 | Modest regression. |
| Java | 10 | 0.283 | 0.272 | 0.238 | −0.034 | Regression — silent-failure fires on Java try/catch patterns. |

Per-language σ at n=10 ≈ 0.018; **all 4 non-TS languages exceed 1σ regression (0.018), and Python/Ruby exceed 2σ (0.036)**. TS is the only non-regression — possibly because the cross-file-impact graph-signal consumption fires there + the .ts file types don't match silent-failure's common-Python `try/except` triggers.

## Per-agent attribution (fuzzy-match)

| Agent | TP | FP | Precision | Note |
|---|---:|---:|---:|---|
| **UNMATCHED** | 25 | **180** | 0.122 | **Up from ~51 in Phase 5.2** — 180 candidates can't fuzzy-match back to a Soliton finding at jaccard ≥ 0.08. The new wirings are emitting findings whose extractor-rewritten candidates don't preserve enough surface form to match. |
| correctness | 29 | 65 | 0.309 | Still the dominant category. |
| security | 7 | 21 | 0.250 | |
| cross-file-impact | 2 | 20 | 0.091 | Graph-signal consumption is producing findings but they're mostly judged FP. |
| hallucination | 1 | 3 | 0.250 | |
| consistency | 0 | 6 | 0.000 | **Unexpected** — should be 0 per skipAgents default. PR #51's silent-failure + comment-accuracy may have category-tagged findings as `consistency` somewhere in their description templates. |
| testing | 1 | 0 | 1.000 | Same flag as consistency. |

The 180 UNMATCHED FP is the headline anomaly. Phase 5.2's UNMATCHED count was ~51. The 4 wirings collectively triple-plus the UNMATCHED FP volume.

## Severity-stratified recall

| Severity | Phase 5.2 | **Phase 5.3** | Δ |
|---|---:|---:|---:|
| Critical | 0.889 (8/9) | **0.889** (8/9) | 0 |
| High | 0.634 (26/41) | 0.537 (22/41) | −0.097 |
| Medium | 0.511 (24/47) | 0.489 (23/47) | −0.022 |
| Low | 0.333 (13/39) | 0.385 (15/39) | +0.052 |

Critical recall is preserved (the v2.1.0 wirings don't drop CRITICAL findings). High recall regressed −0.10 — the synthesizer + realist-check pass may be merging or downgrading borderline-High findings. Low recall actually went up, suggesting the new agents do find some lower-severity issues missed by the v1 swarm.

## Hypothesis: which wiring caused the regression?

This was a 4-agent combined run; the result doesn't isolate per-wiring effects. Plausible decomposition based on Phase 5/5.2 calibration:

1. **silent-failure default-ON** is the strongest suspect. It fires on every Python try/except, every JS Promise.catch, every Java try-catch. CRB's golden set largely doesn't reward error-handling-specific findings; the same per-agent attribution data that motivated Phase 5's `skipAgents: [test-quality, consistency]` likely applies here. Net effect: more candidates emitted, most of them go to UNMATCHED FP.

2. **comment-accuracy default-ON** similarly fires on every PR with comment edits. Same pattern.

3. **realist-check** (this run had it ON) didn't downgrade many criticals (Critical recall unchanged), but may have rephrased finding text in ways that confuse the fuzzy-matcher → contributes to UNMATCHED inflation.

4. **cross-file-impact graphSignals** appears to be neutral-to-positive (TS held), but per-agent precision (0.091) is poor. The graph-driven 90-confidence findings probably need more validation before counting toward the surfaced FINDING_START blocks.

A follow-up isolation run (single-wiring-at-a-time) could attribute the regression. Cost: 4 × $140 = $560 — not currently budgeted.

## Recommendations

1. **Flip `silent-failure` and `comment-accuracy` defaults back to OFF** (or at least off-by-default for CRB runs) until a precision-tuning pass lands. Per `bench/crb/AUDIT_10PR.md` §Appendix A discipline, default-ON agents that emit large UNMATCHED FP volumes should be opt-in, not opt-out.

2. **Realist-check: keep default OFF as currently shipped.** The Step 5.5 wiring is correct; the agent's behaviour just doesn't help CRB F1 at current confidence threshold. Useful for production review (where Mitigated-by: rationale has UX value), but neutral on benchmark.

3. **cross-file-impact graphSignals: keep, but tune severity gate.** TS held at +0.070 vs P3.5 — the lever has signal where the graph backend has language coverage. Java/Go/Ruby would benefit if the sibling `graph-code-indexing` repo packaged language parsers (per §B1). Until then, the graph-derived 90-confidence findings should require corroboration from another agent before promotion to surfaced finding.

4. **Phase 5.2's F1=0.313 remains Soliton's CRB number of record.** Phase 5.3 will not be cited as a published improvement; the writeup is preserved as a negative-result record alongside Phase 3.6/3.7/4c.

5. **POST_V2_FOLLOWUPS.md §A6 closes as ❌ CLOSE.** Future CRB lift work should target the precision axis (FP attribution per agent, similar to the Phase 5 pattern that produced +0.023 from disabling test-quality + consistency).

## Cost accounting

| Activity | Cost |
|---|---:|
| Phase 5.3 dispatch (50 PRs × ~$2.5/PR) | ~$125 |
| Phase 5.3 judge (Azure gpt-5.2) | ~$15 |
| **Total** | **~$140** |

In line with the resume-prompt pre-authorization ceiling of $140 N=1.

## Reproduction

```bash
# Pre-req: .claude/soliton.local.md has all 4 wirings active:
#   tier0.enabled: true
#   spec_alignment.enabled: true
#   graph.enabled: true
#   synthesis.realist_check: true

CONCURRENCY=3 MAX_BUDGET_USD=5 bash bench/crb/dispatch-phase5_3.sh   # ~3 hr, ~$125
bash bench/crb/run-phase5_3-pipeline.sh                               # ~3 min, ~$15
PYTHONUTF8=1 python3 bench/crb/analyze-phase5.py
```

Required: a sibling clone of `withmartian/code-review-benchmark` next to the Soliton repo, plus Azure OpenAI gpt-5.2 managed-identity auth.

## Artifacts

- `bench/crb/phase5_3-reviews/` — 50 Soliton review markdowns (~649 KB total).
- `bench/crb/phase5_3-runs/run1/{evaluations,candidates}.json` — judge output snapshot.
- `bench/crb/phase5_3-runs/run1/analyze.txt` — analyze-phase5.py output.

---

*Filed under: Soliton / CRB / negative results. Written 2026-04-29 evening.*
