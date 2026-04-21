# Soliton CRB Diagnostic — 2026-04-20

Zero-cost analysis of existing CRB artifacts (Phase 3.5 reviews + Phase 3.5.1 evaluations + leaderboard-tool candidates). Written after three consecutive prompt-lever experiments failed to move F1 off 0.277: Phase 4c (0.261), Phase 4c.1 (0.278), Phase 3.5.1 (0.243). Total experiment spend ~$420 with no net F1 gain.

**Purpose:** constrain the next experiment by identifying the actual ceiling, not by guessing levers.

## Headline finding: Soliton emits 3× too many candidates

Candidate count per PR, same 50-PR CRB corpus, all tools:

| Rank | Tool | F1 | min | p25 | median | p75 | p90 | max | mean |
|-----:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | qodo | 0.420 | 2 | 3 | 3 | 5 | 6 | 9 | 3.9 |
| 2 | coderabbit | 0.333 | — | — | — | — | — | 25 | 6.4 |
| 3 | claude | 0.330 | 0 | 1 | 2 | 6 | 7 | 10 | 2.9 |
| 4 | gemini | 0.295 | — | — | — | — | — | 11 | 3.4 |
| 5 | codeant-v2 | 0.294 | — | — | — | — | — | 18 | 3.4 |
| **—** | **soliton** | **0.277** | 0 | 4 | **8** | **13** | **19** | **32** | **9.5** |

**Soliton emits ≈ 3× the F1-leader's volume at every percentile.**

- 52 % of Soliton PRs emit more than 7 candidates (claude's p90).
- 20 % emit ≥15 candidates — a volume the top-3 tools never hit.
- Even Soliton's p25 (4 candidates) exceeds claude's median (2).

This is **uniform over-emission, not a long-tail outlier problem.** Every Soliton PR emits more than its leaderboard peers, regardless of PR size or complexity.

## Secondary finding: recall loss is Low/Medium stylistic, not cross-file

From the current `evaluations.json`, 64 Phase 3.5.1 false-negatives by severity:

| Severity | FNs | Share |
|---|---:|---:|
| Critical | 1 | 2 % |
| High | 16 | 25 % |
| Medium | 21 | 33 % |
| Low | 26 | 41 % |

**Recall on Criticals is ~89 %** (1/9 missed). The recall gap is concentrated on Low+Medium goldens — precisely the stylistic / reviewer-taste territory we dropped nitpicks from in Phase 3.5.

Mean golden text length we missed: 213 chars — short reviewer comments, not deep cross-file logic. The Phase 4a L5 cross-file-retrieval hypothesis ("we're missing cross-file semantic goldens") is largely **falsified by this data** — Phase 4a/4b's structural additions didn't move F1 because the FN bottleneck isn't cross-file awareness; it's reviewer-style coverage at Low/Medium.

## Tertiary finding: correctness + testing dominate emission

From 50 Phase 3.5 review markdown files, category distribution across 332 total rendered findings:

| Category | Count | Share |
|---|---:|---:|
| correctness | 156 | 47 % |
| testing | 75 | 23 % |
| security | 36 | 11 % |
| cross-file-impact | 35 | 11 % |
| consistency | 20 | 6 % |
| hallucination | 5 | 2 % |

**Correctness + testing = 70 % of emission.** With a 4.4:1 FP:TP ratio aggregate, these two categories are most of the FP volume by sheer mass. (Direct category-level FP attribution failed because CRB's step2 extractor rewrites candidate text heavily — matching by title substring only caught 3/385 FPs.)

## Root cause: dispatch × agent-emission × synthesis

The flow:
1. Risk-scorer recommends 2-7 agents for each PR.
2. Each agent emits independently (correctness alone averages ~3 findings per PR).
3. Synthesizer dedupes but **does not aggressively consolidate**.
4. Markdown renderer emits all critical + improvement findings.

Leaders appear to converge on a single "reviewer-level" summary of 2-4 top issues. Soliton's multi-agent parallelism produces 9.5 atomic findings without structural consolidation. The precision tax is baked into the architecture, not the prompts.

## Falsifiable next-experiment proposal — "Phase 5: Top-K filter"

### Hypothesis
Capping post-synthesis findings at K ≈ 3-5 (by severity then confidence) will reduce FPs roughly proportionally to the cut, while retaining most TPs (since TPs skew toward high severity + high confidence). Target aggregate: F1 ≥ 0.30 (+0.023 over Phase 3.5).

### Mechanism
In `agents/synthesizer.md` Step 4 (pre-render), add a top-K filter:

```
Retain:
  ALL critical findings (severity == critical), capped at 3
  + top (K - |kept critical|) improvement findings by confidence
Drop the rest (still emit in JSON output at --output json).
```

Default K = 5. Configurable via `.claude/soliton.local.md` `max_findings_per_pr: 5`.

### Pre-registered ship criteria (aligned with diagnostic prediction)
- ✅ **Ship**: aggregate F1 ≥ 0.30 AND TS F1 ≥ 0.25 (TS was already best at 0.266) AND no language regressed > 0.03 vs Phase 3.5.
- ⚠️ **Hold**: aggregate F1 ∈ [0.28, 0.30] — ship if per-language allows.
- ❌ **Close**: F1 < 0.28 → the over-emission theory is partially wrong; verbose output wasn't the bottleneck, investigate agent-level quality instead.

### Cost
~$140 (same as previous 50-PR runs): $125 claude-p + $15 Azure gpt-5.2 judge.

### Why this lever, not others
- **Per-agent F1 ablation** — would need per-category TP/FP attribution, which step2's text rewriting blocks. Not actionable until CRB pipeline changes.
- **Confidence-threshold bump** — already tried L4 (→85); Phase 3.5.1 data shows 88 % of findings are at ≥85, so bumping higher won't trim much volume. Top-K is the orthogonal lever.
- **Minimal-prose v2.1 retry** — tests a different hypothesis (LLM-verbosity from SKILL.md prose). Could run in parallel, but the top-K cap is higher-confidence per the data.

### Why this might NOT work
1. Many of the top 9.5 candidates may be TPs that the judge splits across multiple goldens → cutting to 5 loses recall faster than it trims FPs.
2. Different PRs legitimately have different optimal K. A hard cap hurts large-diff PRs where 7+ real issues exist.
3. The synthesizer's severity assignment might be miscalibrated, so "top K by severity+confidence" still pushes FPs past TPs.

If (1) or (2) are the reality, Phase 5 closes negative and the next experiment should be per-PR-size-adaptive K, or a judge-calibrated finding reranker.

## What this diagnostic does NOT answer

- **Per-agent F1** — blocked by step2's text rewriting. To get this, we'd need to enrich CRB's candidates with agent-tag metadata before extraction. Separate engineering effort.
- **Absolute TP ceiling** — we know recall is 0.566 in Phase 3.5, but don't know whether that's due to Soliton missing findings the agents could have made, or actual model ceiling on the corpus.
- **Interaction between K and language** — TypeScript's best performance (Phase 3 F1=0.325) came with all nitpicks included; a hard top-K cap could re-regress TS. A per-language K might be needed.

All three are researchable after Phase 5 lands or closes.

## Recommended action

Run **Phase 5: Top-K filter** next. Start with K=5 globally. ~$140, ~1 hour wall clock, one falsifiable hypothesis aligned with the strongest structural signal we've extracted from the CRB data.

If the user prefers to bank Phase 3.5 and move to non-benchmark work (submit to Martian leaderboard, work on other ROADMAP items), the diagnostic also supports that decision — we now understand the ceiling is architectural, not prompt-level.
