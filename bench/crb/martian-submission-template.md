# Martian CRB Upstream Submission Template

**Purpose**: ready-to-use template for §B3 (Martian CRB upstream leaderboard submission). Pre-staged so the submission PR can launch quickly after the auth gate clears (PR #65 OAuth → claude-code-action Console auth).

**Status as of 2026-05-01**: prep work only. The actual submission requires §B3 steps (a)-(e) per `POST_V2_FOLLOWUPS.md` — steps (b)-(d) need auth + CI dogfood; this doc covers step (e) so it's ready to open.

---

## Soliton row for the leaderboard table

Format expected per the leaderboard's existing convention (verify against the upstream `withmartian/code-review-benchmark` repo's `README.md` table format at submission time — schema may have shifted):

| Tool | F1 | Precision | Recall | Track | Architecture | Cost |
|---|---:|---:|---:|---|---|---:|
| **Soliton** | 0.313 | 0.224 | 0.522 | Offline | Multi-agent risk-adaptive (7 default agents; opt-in to 9) | $0.146/PR (real-world projection) |

**Optional differentiator column** (if leaderboard accepts custom metrics): `F1/$ = 2.14` (real-world) / `0.855` (CRB) — first-mover claim per 2026-05-01 SOTA research (no other vendor publishes cost-normalised F1).

## PR title

```
Add Soliton (multi-agent OSS plugin) to offline leaderboard — F1=0.313 + cost-normalised F1
```

## PR body template

```markdown
## Summary

Adds **Soliton** to the offline leaderboard. Soliton is an open-source Claude Code plugin
implementing risk-adaptive multi-agent PR review.

| Metric | Value | Notes |
|---|---:|---|
| F1 | **0.313** | Phase 5.2 corpus, GPT-5.2 judge, single bounded run |
| Precision | 0.224 | |
| Recall | 0.522 | |
| Cost (CRB) | $0.366/PR | Projected per `bench/crb/cost-normalised-f1.md` |
| Cost (real-world) | $0.146/PR | With 60% Tier-0 fast-path eligibility |
| **F1/$ (CRB)** | **0.855** | HOLD per `bench/crb/cost-normalised-f1.md` ship threshold |
| **F1/$ (real-world)** | **2.14** | SHIP — comfortably above the 1.0 threshold |

## Methodology

- **Corpus**: 50 PRs across Python / TypeScript / Java / Go / Ruby (n=10 each)
- **Judge**: Azure OpenAI GPT-5.2 via managed identity, identical config to other CRB submissions
- **Pipeline**: Soliton review → step2 extract candidates → step2.5 dedup → step3 judge against goldens
- **Noise envelope** (PR #48 calibration): σ_F1 = 0.0086 within-run; σ_Δ paired = 0.0122 between-run; the published 0.313 was on the low edge of the 4-run mean (0.321)
- **Architecture**: 7 default review agents (correctness, security, hallucination, test-quality skipped, consistency skipped, cross-file-impact, historical-context) + risk-scorer + synthesizer. v2 feature flags (Tier-0 / Spec-Alignment / Graph signals / Realist Check / silent-failure / comment-accuracy) all default OFF per CRB measurement evidence.

## Cost-normalised F1 (first-mover claim as of 2026-05-01)

To our knowledge, no other vendor on the Martian CRB leaderboard publishes F1/$. Soliton derives both CRB-scoped (0.855) and real-world projection (2.14) numbers in `bench/crb/cost-normalised-f1.md`. We propose this as a useful complementary metric for procurement-grade evaluations: raw F1 measures review quality on hard cases; F1/$ measures cost-efficiency of the review pipeline.

## Reproduction

The dispatch script (`bench/crb/dispatch-phase5.sh`) and pipeline (`bench/crb/run-phase5_2-pipeline.sh`) reproduce the result deterministically given the same Azure OpenAI config + Soliton repo at commit [TODO: pin commit SHA at submission time]. See `bench/crb/RESULTS.md` § Phase 5.2 for the full per-language slice.

## Limitations

- **Single bounded run**: F1=0.313 was a single dispatch + judge invocation. Subsequent re-runs at fixed input show σ_F1 = 0.0086, but cross-corpus replication has not been independently verified.
- **Internal harness vs upstream pipeline**: Soliton's CRB harness is a local simulation of the offline-track scoring pipeline. Upstream verification (running on the canonical Martian CRB infrastructure) is what this submission unlocks.
- **Cost-normalised projection**: per-PR cost is derived from `rules/model-tiers.md` token estimates and 60% Tier-0 eligibility measured on Spring PetClinic (n=10). A signal-grade measurement is gated on `--mode pr-review` harness instrumentation surfacing per-Agent token usage; tracked under POST_V2_FOLLOWUPS §C2 Phase 2.

## License

Soliton is MIT-licensed at https://github.com/andyzengmath/soliton. Plugin manifest: `.claude-plugin/plugin.json` (v2.1.2).
```

## Methodology citations (for reviewers who want depth)

- **σ envelope**: PR #48 — judge-noise calibration measuring σ_F1 = 0.0086 across 4 re-runs
- **Phase 5.2 result**: `bench/crb/RESULTS.md` § Phase 5.2 (footnote-strip counterfactual, F1=0.313)
- **Cost derivation**: `bench/crb/cost-normalised-f1.md` (Phase 1 schema PR #82 + Phase 2 derivation PR #83)
- **C1 enterprise validation**: `bench/graph/enterprise-java-dogfood.md` (PetClinic scout, 4 oracle-grade catches) + `bench/graph/enterprise-camel-dogfood.md` (Apache Camel full-swarm, 5 CRITICAL + 19 IMPROVEMENT)
- **Default skipAgents rationale**: Phase 5 attribution (test-quality + consistency contributed 31% of CRB FPs at 2.5% combined precision)
- **v2 feature-flag default-OFF rationale**: Phase 5.3 evidence (PR #68; combined v2.1.0 wirings regressed F1 by 0.045 at 5.2σ_Δ paired)

## Submission checklist (pre-flight before opening upstream PR)

- [ ] §B3 step (a): claude-code-action Console auth unblocked (PR #65 merged)
- [ ] §B3 step (b): 50 benchmark PRs forked into a GitHub org where Soliton is installed
- [ ] §B3 step (c): `step1_download_prs.py` patched with `_NON_BOT_TOOLS += "soliton"`; `step0_fork_prs.py` skips `disable_actions` and injects `soliton-review-bench.yml`
- [ ] §B3 step (d): pipeline runs cleanly on the upstream Martian harness (not just Soliton's local simulation); output benchmark_data.json reproduces F1=0.313 ±σ_F1
- [ ] **Pin commit SHA** in the submission PR body (replace [TODO: pin commit SHA] above)
- [ ] **Re-verify SOTA picture at submission time** — leaderboard moves daily per Mar–Apr 2026 vendor blog cadence; the F1/$ first-mover claim may need refresh
- [ ] **Confirm leaderboard table schema** matches the row format above (verify against upstream README at submission time)

## What this doc is NOT

- A commitment to specific timing — submission is gated on §B3 step (a) which is a user-action.
- An assertion that the upstream pipeline will produce identical numbers — Soliton's local simulation may diverge from the canonical Martian harness; the actual submission run is what validates the F1=0.313 claim externally.
- A pre-authorization for the multi-day human-time investment in §B3 steps (b)-(d). That remains a separate decision.

---

*Filed under: Soliton / bench / CRB / upstream-submission. Pre-staged 2026-05-01 to enable rapid §B3 step (e) launch when steps (a)-(d) clear.*
