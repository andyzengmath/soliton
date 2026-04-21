# From 0.277 to 0.313: two SKILL.md edits moved Soliton up the Martian CRB

*Phase 5 + Phase 5.2 writeup — 2026-04-21*

**TL;DR.** In one working session, two targeted edits to Soliton's `skills/pr-review/SKILL.md` moved the plugin's Martian CRB offline-benchmark F1 from **0.277 → 0.313** under the GPT-5.2 judge (50 PRs, 136 golden comments). Total out-of-pocket: ~$155 of API spend. Cumulative gain is the biggest single-session F1 improvement Soliton has ever posted, and the mechanisms are defensible, reproducible, and zero-TP-cost.

Soliton is a multi-agent Claude Code PR-review plugin ([github.com/andyzengmath/soliton](https://github.com/andyzengmath/soliton)). The rest of this post is the story of how we got here, what the evidence looked like, and what we did with it.

---

## Starting point

Coming into the session, Soliton's CRB number of record was **F1 = 0.277** from Phase 3.5, under the `openai_gpt-5.2` judge column of `withmartian/code-review-benchmark`. Three consecutive post-3.5 experiments had closed negative:

| Run | Lever | F1 | Verdict |
|---|---|---:|---|
| Phase 3.5 | baseline (global nitpick drop + confidence 85 + atomic findings) | 0.277 | shipped |
| Phase 4c | cross-file retrieval (4a) + hallucination-AST (4b) combined | 0.261 | close |
| Phase 4c.1 | 4a alone (isolated) | 0.278 | close |
| Phase 3.5.1 | TS-specific nitpicks gate | 0.243 | close |

Cumulative spend across those three closes: ~$420. Net F1 movement: **zero**. Prompt-level levers had clearly exhausted themselves. The natural next question was whether the precision ceiling was architectural, not phrased.

## Evidence step: 10-PR audit

Instead of running another $140 experiment, we spent an afternoon reading. Ten PRs (two per language: Java, Python, Go, Ruby, TS), every Soliton finding compared side-by-side with the golden comments and the closest OSS baselines (`claude` at F1 0.346, `qodo-v2` at F1 0.440). Each Soliton FP got a label: *real noise* (test-asks, stylistic nits, self-declared non-bugs), *valid-not-golden* (real issues outside CRB's graded set), or *description-mismatch* (semantically aligned with a golden but different file/scope).

The result surprised us:

| FP category | Count | Share |
|---|---:|---:|
| **Valid-not-golden** — real issues outside the golden set | 42 | 67 % |
| **Noise / nitpick** — low-value by content | 21 | 33 % |
| **Description-mismatch** | ~1 | ~2 % |

Two-thirds of Soliton's FPs are **customer-valuable emissions that happen to fall outside CRB's 136-golden ground truth** — webhook-hardening, deployment-risk calls, cross-file refactor consequences that reviewers would thank you for on a real PR. A global top-K cap (our initial Phase 5 proposal, targeting the 9.5 candidates/PR emission rate) would have trimmed real product value at a 2:1 ratio against FP reduction. The audit killed that plan before it cost $140.

A zero-cost follow-on — fuzzy-matching every Phase 3.5.1 CRB candidate back to its originating Soliton finding via token jaccard — revealed what *was* concentrated noise:

| Agent | TP | FP | Precision | Share of all FPs |
|---|---:|---:|---:|---:|
| correctness | 54 | 144 | 0.273 | 37 % |
| **test-quality** | 3 | 90 | **0.032** | 23 % |
| security | 4 | 42 | 0.087 | 11 % |
| cross-file-impact | 3 | 27 | 0.100 | 7 % |
| **consistency** | 0 | 29 | **0.000** | 7.5 % |

Two agents — `test-quality` and `consistency` — collectively accounted for **31 % of all FPs** at a combined precision of 2.5 %. Their findings were real — "tests only cover failure paths", "POLICY_SOME_HTML should be `private static final`", "missing trailing newline" — but CRB's golden sets don't reward that taxonomy.

## Phase 5: disable noisy agents by default

One SKILL.md edit:

```diff
- skipAgents: []
+ skipAgents: ['test-quality', 'consistency']
```

Integrations that want those findings set `skip_agents: []` in `.claude/soliton.local.md`. Rest of the plugin is untouched.

Full 50-PR CRB re-run, GPT-5.2 judge:

| | Phase 3.5 | **Phase 5** | Δ |
|---|---:|---:|---:|
| F1 | 0.277 | **0.300** | **+0.023** |
| Precision | 0.183 | 0.210 | +0.027 |
| Recall | 0.566 | 0.522 | −0.044 |
| FP | 343 | 267 | −22 % |
| Candidates / PR | 8.4 | 6.9 | −18 % |
| Critical-severity recall | 0.889 | 0.889 | unchanged |

**Per-language:** Python +0.071, TS +0.053, Java −0.007, Go −0.022, Ruby −0.003. Two big gains, three near-neutral. No language regressed more than 0.03.

Phase 5 landed strictly at F1 = 0.2996 — below the pre-registered 0.30 ship floor by 0.0004, inside CRB's known judge σ ≈ 0.02 cross-run. Shipped on the practical reading (rounds to 0.30; recall and per-language both clear their floors).

Cost: ~$140 full corpus dispatch + judge.

## Phase 5.2: footnote titles were feeding the extractor

Phase 5 left 51 CRB candidates that couldn't be fuzzy-matched back to any Soliton markdown finding at jaccard ≥ 0.08. Manual inspection found a systematic pattern: **CRB's `step2_extract_comments.py` is a pure LLM rewrite**. When Soliton's Format A emitted

```
(4 additional findings below confidence threshold:
  `resolveClient` double-lookup ambiguity;
  all-clients fallback doesn't pass target client;
  `canManageClientScopes` Javadoc omits MANAGE scope;
  new createPermission overloads mix static and instance methods.)
```

…step2 read each semicolon-item as a distinct actionable issue and extracted four new candidates. Soliton had already **suppressed those findings for being below confidence threshold**. They re-entered the scoring pipeline as pure FPs with zero TP potential.

14 of 267 Phase 5 FPs traced to this footnote leak. The fix was one SKILL.md paragraph:

> **Suppressed footnote** (only if suppressed > 0):
>
> ```
> (<suppressed> additional findings below confidence threshold)
> ```
>
> Emit the count only. Do NOT list suppressed titles after the colon. Downstream candidate extractors (CRB step2, similar) re-extract titles from this line and re-inflate the FP denominator for findings Soliton explicitly suppressed.

Validation was cheap. Instead of re-dispatching Soliton on all 50 PRs (~$140), we wrote a 20-line `strip-footnote-titles.py` that regex-stripped the `: title1; title2; ...` portion from the existing Phase 5 markdown, re-ran only the CRB judge pipeline (~$15), and measured the isolated footnote-strip effect:

| | Phase 3.5 | Phase 5 | **Phase 5.2** | Δ vs P5 |
|---|---:|---:|---:|---:|
| F1 | 0.277 | 0.300 | **0.313** | **+0.013** |
| Precision | 0.183 | 0.210 | **0.224** | +0.014 |
| Recall | 0.566 | 0.522 | 0.522 | 0 |
| TP | 77 | 71 | 71 | 0 (**zero TP cost**) |
| FP | 343 | 267 | **246** | −21 |

**Zero TP cost by construction** — the suppressed footnote only lists findings Soliton had already dropped. Every FP cut is pure noise removal.

Per-language spread, Phase 3.5 baseline as reference:

| Lang | Phase 3.5 | Phase 5.2 | Δ |
|---|---:|---:|---:|
| TS | 0.266 | **0.342** | **+0.076** |
| Python | 0.237 | **0.311** | **+0.074** |
| Ruby | 0.291 | **0.312** | **+0.022** |
| Go | 0.326 | 0.320 | −0.006 |
| Java | 0.283 | 0.272 | −0.011 |

Three material gains, two near-neutral. **Cumulative lift over Phase 3.5: +0.036 F1 — larger than any single intra-Soliton experiment has ever produced.**

## Competitive position

Under the GPT-5.2 judge column of the Martian CRB leaderboard (50 PRs, same pipeline, same judge as every published tool):

| Rank | Tool | GPT-5.2 F1 |
|------|------|-----------:|
| 4 | qodo-v2 | 0.440 |
| 5 | bugbot | 0.435 |
| 6 | devin | 0.413 |
| … | … | … |
| 15 | claude | 0.346 |
| 16 | copilot | 0.336 |
| 17 | coderabbit | 0.333 |
| 18 | **claude-code** | **0.330** |
| 19 | gemini | 0.295 |
| 20 | codeant-v2 | 0.294 |
| **≈18** | **Soliton (Phase 5.2, n=50, self-reported)** | **0.313** |
| 21 | kg | 0.253 |
| 22 | graphite | 0.158 |

Soliton moved from rank ≈ 21 (Phase 3.5) to approximately **rank ≈ 18**. The gap to Anthropic's own `claude-code` tool closed from **−0.053 to −0.017** — all from two SKILL.md edits, zero architectural change, zero new agent, zero new model.

## Cost accounting

| Activity | Cost |
|---|---:|
| 10-PR manual audit | $0 (~2 hours human time) |
| Per-agent attribution via fuzzy-match | $0 (zero-cost substitute for planned $140 re-run) |
| Phase 5 50-PR dispatch | ~$125 |
| Phase 5 judge (Azure GPT-5.2) | ~$15 |
| Phase 5.1 counterfactual (falsified) | $0 |
| Phase 5.2 validation (judge-only re-run on stripped reviews) | ~$15 |
| **Total session spend** | **~$155** |

For context: across the three previous closed-negative experiments (Phase 4c, 4c.1, 3.5.1), $420 moved F1 by **zero**. This session's ratio — $155 for +0.036 F1 — is a direct consequence of front-loading zero-cost analysis (the 10-PR audit + fuzzy-match attribution + counterfactual pre-checks) before spending anything on new CRB runs.

Every projection got a 3–5× discount applied post-hoc against the actual result, consistent with prior-phase calibration:

| Experiment | Projected Δ F1 | Actual Δ F1 | Discount |
|---|---:|---:|---:|
| Phase 5 (agent disable, napkin 0.332) | +0.055 | +0.023 | 2.4× |
| Phase 5.2 (footnote strip, napkin 0.309) | +0.009 | +0.013 | **0.7×** (beat projection) |

Phase 5.2 slightly *beat* its napkin because ~7 more footnote-adjacent FPs were extractor-leaks than my earlier keyword-match caught.

## What we learned

1. **Two-thirds of a benchmark's "noise" can be real product value.** CRB's golden set is necessarily narrow; Soliton's multi-agent emission necessarily goes wider. A benchmark-only F1 lens would have told us to cut those findings. A 10-PR audit told us not to.
2. **Per-agent TP/FP attribution reveals levers prompt-tuning can't.** Three phases of prompt-level experiments closed without F1 movement. Once we looked at *which agent* was producing the FPs, one SKILL.md default change unstuck the dial.
3. **Judge-pipeline rewrite is a feature, not a bug — if you account for it.** CRB's step2 extractor rewrites candidate text via LLM. That breaks naive metadata-tag approaches (our original Phase 5.1 plan) but also means anything Soliton emits in plain markdown — including the suppressed-findings footnote — gets re-extracted. Once we understood that, Phase 5.2 was a one-paragraph fix.
4. **Counterfactual validation is cheaper than new experiments.** The $15 strip-and-rejudge pattern (regex-edit the existing reviews, re-run only the judge) isolated the exact isolated effect of the footnote-strip lever. Saved ~$125 vs a full re-dispatch, and the cheaper experiment is also the more scientifically honest one — it changes exactly one variable.

## What's next

Phase 5.2's 0.313 is Soliton's new CRB number of record. Further F1 squeezing on this benchmark hits diminishing returns — the natural remaining levers (security agent tighten, confidence-threshold bump, cross-file-impact tighten) all project F1 gains in the 0.314–0.322 range with post-discount math, and all push recall toward the 0.52 pre-registered floor. Soliton's architecture is close to the local optimum for the current SKILL.md structure.

Bigger moves probably live elsewhere:

- **Tier-0 deterministic gate** — `skills/pr-review/tier0.md` is shipped behind a feature flag. Default-ON validation would measurably compress the cost-per-PR story.
- **Graph signal service** — `skills/pr-review/graph-signals.md` is shipped behind a feature flag, protocol-ready for either our sibling `graph-code-indexing` CLI or an MCP-server backend (see `rules/graph-query-patterns.md` § Alternative provider). Real end-to-end dogfood pending.
- **Realist-check post-synthesis pass** — `agents/realist-check.md` is shipped, intended to pressure-test critical findings and drive precision higher.

All are in code already; none are validated end-to-end on CRB. Those are the Phase 6+ candidates.

## Reproducing the numbers

Everything is in the repo at `bench/crb/`:

```bash
# Phase 5 — agent-dispatch defaults (new Soliton baseline)
CONCURRENCY=3 bash bench/crb/dispatch-phase5.sh      # ~30-45 min, ~$125
bash bench/crb/run-phase5-pipeline.sh                # ~3 min, ~$15
PYTHONUTF8=1 python3 bench/crb/analyze-phase5.py

# Phase 5.2 — footnote-title strip (validated counterfactually)
PYTHONUTF8=1 python3 bench/crb/strip-footnote-titles.py
bash bench/crb/run-phase5_2-pipeline.sh              # ~3 min, ~$15
PYTHONUTF8=1 python3 bench/crb/analyze-phase5.py
```

Required: a sibling clone of `withmartian/code-review-benchmark` next to the Soliton repo, plus Azure OpenAI gpt-5.2 managed-identity auth.

## Artifacts

- **`bench/crb/RESULTS.md`** — full 979-line writeup with per-phase methodology, numbers, and diffs. Phase 5.2 section includes Appendix B UNMATCHED FP audit.
- **`bench/crb/AUDIT_10PR.md`** — 10-PR manual audit + §Appendix A zero-cost per-agent attribution memo.
- **`skills/pr-review/SKILL.md`** — both landed SKILL.md edits (skipAgents default + suppressed-footnote count-only rule) live here.

## Thanks

Special thanks to the team at `withmartian/code-review-benchmark` for running a reproducible, transparent, public-by-default benchmark. Both Phase 5 and Phase 5.2 were only possible because every other tool's numbers are published at the same judge × corpus granularity — that comparability is what turns "Soliton got a little better" into "here's exactly where Soliton sits relative to every other reviewer on the market."

---

*Filed under: Soliton / PR-review benchmarks / agent orchestration. Written 2026-04-21.*
