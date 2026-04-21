# Soliton CRB Manual Audit — 10 PRs — 2026-04-20

Zero-cost qualitative audit of Phase 3.5 Soliton reviews against goldens and the two closest CRB baselines (`claude` as base-model parity, `qodo-v2` as F1 leader at 0.440 under GPT-5.2). Written to sharpen the Phase 5 "Top-K filter" proposal after three close verdicts (Phase 4c 0.261, 4c.1 0.278, 3.5.1 0.243) left Phase 3.5's F1=0.277 unmoved.

**Methodology.** For each of 10 PRs (2 per language) I read Soliton's `phase35-reviews/<slug>.md`, the goldens from `code-review-benchmark/offline/golden_comments/*.json`, and the `claude` / `qodo-v2` candidates from `results/openai_gpt-5.2/candidates.json`. Each Soliton finding was scored TP (matches a golden) / FP (valid-not-golden, noise, or description-mismatch) manually. Data consolidation script lives at `bench/crb/audit10pr-data.json` + `bench/crb/audit10pr-split/*.json` (gitignored; rebuild via inline Python in the pipeline folder).

**Sample.** 50 goldens, 10 PRs, 2 per language — `keycloak-37634`, `keycloak-37429`, `sentry-93824`, `sentry-77754`, `grafana-79265`, `grafana-103633`, `discourse-graphite-4`, `discourse-graphite-10`, `cal.com-10967`, `cal.com-11059`. Biased toward PRs well-documented in Phase 2/3 writeups so calibration against prior numbers is available.

## Headline numbers on the audit sample (manual, no step2)

| | Soliton | claude | qodo-v2 |
|---|---:|---:|---:|
| Candidates (Σ over 10 PRs) | 92 | 49 | 66 |
| Mean candidates / PR | 9.2 | 4.9 | 6.6 |
| TPs (candidate-level, manual match) | 29 | 20 | 27 |
| Goldens covered (distinct) | 27 / 44 | 20 / 44 | 27 / 44 |
| Manual precision | 0.315 | 0.408 | 0.409 |
| Manual recall | 0.614 | 0.455 | 0.614 |
| Manual F1 | 0.416 | 0.431 | 0.491 |

Gap to CRB-step2-pipeline Phase 3.5 F1=0.277 reflects CRB step2's sub-issue extraction (which multiplies Soliton's long bullets into 2-3 candidates each) plus GPT-5.2 stricter semantic match than my manual read. Even with that caveat, **qodo-v2 catches the same 27 goldens Soliton catches with ~29 % fewer candidates** — a clean "quality of selection, not volume" signal.

## FP taxonomy (63 Soliton FPs)

Manually classified across all 10 PRs:

| Category | Count | Share | Description |
|---|---:|---:|---|
| **Valid-not-golden** | **42** | **67 %** | Real issue (security, correctness, cross-file). Outside CRB's graded set. Customer value is real; judge-F1 value is zero. |
| **Noise / nitpick** | 21 | 33 % | Low-value: test-asks ("add a test for X"), stylistic ("should be private static final"), tautology, no observable bug. |
| **Description-mismatch** | ~0–1 | <2 % | Semantically aligned with a golden but different file/scope → judge likely rejects. |

The 42:21 ratio is the load-bearing finding. **Two-thirds of Soliton's FPs are customer-valuable emissions outside the CRB golden set** (security hardening suggestions, deployment-risk callouts, cross-file refactor-consequence warnings that reviewers would thank you for on a real PR but that don't appear in CRB's 44-golden ground truth).

### What "valid-not-golden" looks like (representative examples)

- `cal.com-11059` S13–S16: no POST-method check, no rate limit, no TLS enforcement on sync endpoint, TOCTOU on credential upsert. All real webhook-hardening gaps. CRB's 5 goldens on this PR are all about the Zod schema / token-refresh bugs; the webhook surface is out of scope.
- `keycloak-37634` S4–S5: removing public SPI methods without a deprecation path. Real breaking-change warnings. Goldens don't cover SPI compatibility.
- `discourse-graphite-4` S6–S10: `force: true` on historical migration, Disqus `created_at` dropped, unrescued fetch failures, stale spec mock. All real production bugs. Goldens focus on the XSS/SSRF chain.

### What "noise" looks like

- `sentry-77754` S8–S11: four consecutive "add a test for X" findings, including one that flags another test as a tautology. Real test-quality ask but low-value for a PR review.
- `keycloak-37429` S2–S3, S5–S6: `private static final` performance nit, `RuntimeException → MojoExecutionException` style nit, bundle re-read performance nit, regex dash ambiguity that Soliton itself notes "has no observable bug today". Half of the FPs on this PR are self-declared non-bugs.

## Per-PR highlights (the insights, not the full scorecard)

1. **`keycloak-37429` (4 goldens, Soliton 6 / claude 1 / qodo-v2 3):** the PR is a translation-content update. The golden set is 3 translation-correctness comments + 1 method-name typo. Soliton missed 3 of 4 (translation content is out-of-scope for a code-review agent by design). Claude's **single** candidate was the typo — the only golden any Claude-family tool could reasonably catch. Soliton's 5 non-golden FPs are all stylistic nits on the sanitizer class. **This PR is a PR-type-classification problem, not a volume problem.**

2. **`sentry-93824` (5 goldens, Soliton 3 / claude 3 / qodo-v2 6):** Soliton was the **terse** tool here (3 findings!) and still missed 3/5 goldens. qodo-v2 emitted 2× Soliton's volume and caught 3/5 (same coverage). **Terseness did not produce recall.** The bottleneck on this PR is which agents get dispatched — Soliton's correctness/testing agents fired but missed the `isinstance(SpawnProcess, multiprocessing.Process)` golden and the `break skips termination` golden. Both are cross-function reasoning gaps, not emission-volume problems.

3. **`sentry-77754` (4 goldens, Soliton 11 / claude 2 / qodo-v2 2):** Soliton 5.5× the leaders' volume, same goldens-caught count (2). Of Soliton's 9 FPs, **5 are noise** (all test-asks) — the noise rate hits 45 % on this PR, the highest in the sample.

4. **`cal.com-11059` (5 goldens, Soliton 16 / claude 6 / qodo-v2 11):** Soliton's largest emission in the sample, 12 FPs — but **10 of those 12 are valid-not-golden** (webhook hardening, Salesforce-specific correctness, security-infra). qodo-v2 with 11 candidates caught all 5 goldens; Soliton with 16 caught 4. **Volume doesn't predict FP quality** — qodo-v2's 11 findings are as voluminous as Soliton's 16 but denser on the graded set.

5. **`discourse-graphite-4` (6 goldens, Soliton 13 / claude 10 / qodo-v2 18):** qodo-v2 **outvolumed** Soliton here and caught all 6 goldens (vs. Soliton's 5). Volume alone is not what costs F1; target selection does.

6. **`grafana-103633` (2 goldens, Soliton 6 / claude 6 / qodo-v2 3):** Soliton and qodo-v2 both caught both goldens; Soliton just emitted 2× the candidates. 4 of Soliton's 6 findings are FPs (2 valid-not-golden, 2 noise). qodo-v2's 3 findings cover the goldens densely.

## Against Goal A's top-K-filter hypothesis

Goal A predicts: "Capping post-synthesis findings at K ≈ 3–5 by severity then confidence will reduce FPs proportionally while retaining most TPs." **The audit data does not support this mechanism as a clean F1 win.**

### What a K=5 cap would actually do on this 10-PR sample

Estimating severity/confidence distribution from the Phase 3.5 markdown (critical + improvement-by-confidence-desc), Soliton averages ~2–3 criticals + 3–5 improvements + 0 nitpicks per PR (nitpicks already gated out since Phase 3.5). A K=5 cap (ALL criticals up to 3, then top 2 improvements by confidence) would:

- **Retain** all criticals (where most Soliton TPs sit) and the top 2 improvements.
- **Drop** ~4–5 findings per PR — predominantly lower-confidence improvements.

Those dropped findings split by category:

| Dropped bucket | Estimated count across 10 PRs | TP value | Customer value |
|---|---:|---:|---|
| Lower-confidence improvements, valid-not-golden | ~22 | 0 | real (most "dropped FPs" are real-issue reports that reviewers find useful) |
| Lower-confidence improvements, noise | ~18 | 0 | none |
| Lower-confidence improvements, TP on Low/Medium golden | ~5 | real | real |

Net F1 impact: precision ↑ ~0.05, recall ↓ ~0.03 (from losing 5 Low/Medium TPs). **F1 ≈ +0.02**, well below the pre-registered ship floor (+0.023 required). And the **customer-value cost is 22 real findings silenced** per 10 PRs — ~2.2 per PR on average — the exact thing that makes Soliton useful in real-world review.

### Why the mechanism is shaky

1. **TP density is roughly uniform by severity tier for Soliton.** G4-Low on `keycloak-37634` (RuntimeException too broad) matched Soliton's S9 which is an **improvement-testing** finding, not a critical. G3-Low on `cal.com-10967` (redundant optional chaining) is exactly the sort of finding that gets cut first. G5-Low on `cal.com-10967` (Calendar.createEvent signature) was already **missed** by Soliton — the ordering doesn't help because it's already absent from Soliton's emission.

2. **67 % of FPs are customer-value, not CRB-gameable.** Cutting them trims F1 tax but trims real product value at 2:1. This is the "valuable-noise trap."

3. **Per-PR variance is huge.** `sentry-93824` would be untouched by K=5 (Soliton only emits 3 there); `cal.com-11059` would lose 11 of 16 — and that PR's 5/5-goldens-covered rate is already above-average. Hard cap punishes the PRs Soliton handles best.

## Sharper next-lever candidates (data-driven)

Re-ranked vs. Goal A:

### Candidate X: Targeted-noise suppression at the agent level (~$140)

Not a global K-cap — surgical. Target the 21 noise FPs by pattern:

- **Testing-nit suppressor** (rules/): if a Soliton finding's category is `testing` and its title contains "no unit tests" / "add a test for" / "covers only the X path" AND there's no correctness/security finding in the same file, drop it from markdown output. Keeps JSON output intact (`--output json` preserves all findings per Phase 3.5 nitpick convention).
- **Stylistic-nit suppressor** (rules/): drop improvement-severity findings whose body contains `"no observable bug"`, `"deferred to a later"`, or whose fix is pure naming / modifier style (`private static final`, `RuntimeException → MojoExecutionException`). These are self-declared low-value in the finding body.

Estimated effect on audit sample: removes ~15–18 of the 21 noise FPs. Valid-not-golden FPs untouched. TPs untouched.

Projected F1 lift (conservative, with IMPROVEMENTS.md 3–5× calibration discount applied): **+0.015 to +0.025**.

Against ship criteria: lands in **hold** band (0.28–0.30), so no certain ship. But the mechanism is defensible in a writeup either way — it removes low-value findings without touching customer-value emissions.

### Candidate Y: PR-type classification → agent dispatch gating (~$140)

`keycloak-37429` was a translation-content PR where Soliton emitted 5/6 noise and missed 3/4 goldens; the right behavior is to skip correctness/testing/hallucination and run only security + consistency. The risk-scorer already produces `recommendedAgents`, but doesn't factor in content-only signals.

Mechanism: add a "content-PR" classifier pass (or extend risk-scorer) that detects when >80 % of changed lines are in `.properties`, `.po`, `.md`, translation `*.json`, and fixtures, then emits a short dispatch list (security + consistency) and a low-confidence threshold. `agents/risk-scorer.md` is the hook.

Projected: removes most noise on content-PRs; doesn't change Soliton's behavior on normal PRs. Audit sample has 1/10 content-PR, so population-level F1 lift is small (~+0.005 weighted). But the review quality jump on content-PRs is dramatic.

Against ship criteria: probably **close** on aggregate F1 (too few content PRs in CRB to move the needle) but **ship on per-language** if Java's noise drops.

### Candidate Z: Per-agent TP/FP attribution re-run (Goal C1 from PHASE_5_RESUME_PROMPT.md, ~$140)

The audit surfaces one thing the diagnostic couldn't: **of 63 FPs, which agents emit them?** My finding-body parse attributes them loosely to `category`, but `category` ≠ `agent` (correctness agent can emit testing findings, etc.). CRB step2's text rewriting currently blocks per-agent TP/FP.

The clean way to unblock this is to enrich Soliton's markdown output with trailing metadata tags:

```
<!-- soliton-agent:correctness cat:correctness conf:90 sev:improvement -->
```

Step2 preserves comments by default (most LLM extractors pass them through as context). Then Phase 3.5.1's candidates.json would have per-candidate agent attribution, and a re-run plus evaluation.json enrichment would give a per-agent TP/FP/FN table.

The motivation: if 60 %+ of the 21 noise FPs come from **one** agent (say testing), the agent-level fix is cheaper than a global rule. The audit suggests testing IS the noise leader (`sentry-77754` S8–S11 are all testing; `discourse-10` S3–S5 are all testing; `grafana-79265` S6 is testing). Per-agent data would confirm.

Against ship criteria: this is a diagnostic investment, not a ship experiment. Output is a memo + structured data, not an F1 delta. Phase 5 (candidate X or Y) becomes higher-confidence after this.

## Recommendation

**Do NOT run Goal A (global top-K cap) as specified.**

The audit's strongest signal is that 67 % of Soliton's FPs are customer-valuable real findings outside the golden set — cutting them improves CRB F1 marginally but silences real product value. The mechanism also has too much variance across PRs (`sentry-93824` is untouched; `cal.com-11059` loses 11/16).

**Ranked alternatives, all ~$140 each:**

1. **Candidate X** (targeted-noise suppression via rules/testing-nit-pattern + rules/stylistic-nit-pattern). Minimal prose footprint (single new rules file each), ~0.015–0.025 projected F1. Pre-registered ship: F1 ≥ 0.29, recall ≥ 0.55, no language reg > 0.03.
2. **Candidate Z** (per-agent attribution re-run via markdown metadata tags preserved through step2). Not an F1 experiment — diagnostic. Output is a ranked per-agent FP table that **sharpens** the Candidate X design before spending on it.
3. **Candidate Y** (content-PR classifier in risk-scorer). Small aggregate F1 lift, big per-PR-type quality jump. Probably a Phase 5.x rather than a primary experiment.
4. **Bank 0.277 and submit to Martian** (Goal B from resume prompt). Defensible given three closes + calibrated audit showing the structural F1 ceiling is real.

**Pre-registered ship criteria for whichever is picked** (aligned with Phase 4 / 4c.1 precedent):

| Outcome | Aggregate F1 | Recall | Per-language | Action |
|---|---:|---:|---|---|
| ✅ Ship | ≥ 0.29 | ≥ 0.55 | No reg > 0.03 | Replace Phase 3.5 |
| ⚠️ Hold | 0.28–0.29 | 0.52–0.55 | — | Docs PR with writeup |
| ❌ Close | < 0.28 | < 0.52 | Any > 0.05 | Documented negative |

**Most defensible single next action:** Candidate Z ($140, diagnostic-only, no ship verdict needed) → if the per-agent table fingers `testing` as the ≥50 % source of noise FPs, Candidate X becomes high-confidence and the rules are designed to target the right agent specifically. If the per-agent table shows noise is spread uniformly, Candidate X-by-pattern still applies but the design has to be category-level rather than agent-level.

If the operator wants a ship-or-close verdict immediately, **Candidate X** is the safest single-shot experiment: the 15–18 FPs targeted are self-declared noise, removal risk is bounded, and the writeup lands on main either way.

## What this audit does NOT answer

- **True per-agent F1** — blocked without the metadata-tag enrichment (Candidate Z). All per-agent attribution in this memo is inferred from finding `category` strings, which is noisy.
- **step2 multiplication factor per finding-length** — the F1 gap between my manual 0.416 and Phase 3.5's 0.277 is partly step2 + partly stricter GPT-5.2 semantic match. A single-PR ablation on one "long-body" finding vs. a "short-body" equivalent would quantify the step2 tax.
- **Per-PR K-optimization** — a K that adapts to PR complexity (size, agent count, critical count) might outperform a global K, but this audit didn't simulate it.

All three are follow-ups after the Phase 5 decision lands.

## Reproduction

```bash
# Rebuild audit data (from repo root):
python3 - <<'EOF'
# See the inline Python block used to generate bench/crb/audit10pr-data.json
# and the per-PR splits in bench/crb/audit10pr-split/*.json.
# (Not committed — regenerate from code-review-benchmark repo at the neighboring path.)
EOF
```

Source files consulted (read-only):
- `bench/crb/phase35-reviews/*.md` (Soliton Phase 3.5 emission)
- `../code-review-benchmark/offline/golden_comments/*.json` (CRB ground truth)
- `../code-review-benchmark/offline/results/openai_gpt-5.2/candidates.json` (published leaderboard candidates for `claude` and `qodo-v2`)

---

## Appendix A · Zero-cost per-agent attribution (all 50 PRs)

Added 2026-04-20 after the 10-PR audit. Goal A from PHASE_5_RESUME_PROMPT.md was to test over-emission via top-K; Goal C1 was to get per-agent TP/FP attribution via a $140 re-run with metadata tags. **Reading CRB's `step2_extract_comments.py` revealed that step2 is a pure LLM rewrite** (prompt at lines 22–49), so embedding metadata tags in Soliton's markdown would NOT survive extraction. The $140 re-run would not produce usable per-agent data.

**Zero-cost alternative executed instead:** for each of 456 candidates in `azure_gpt-5.2/candidates.json` (50 PRs of Soliton output, Phase 3.5.1 state which differs from 3.5 only in TS-specific nitpicks), I fuzzy-matched the step2-extracted candidate text back to its originating Phase 3.5.1 markdown finding via token-jaccard (threshold 0.08, file-basename bonus +0.15). The matched finding's `[category]` prefix serves as agent attribution. Match rate: **88.6 %** (404/456 candidates attributed).

The CRB judge's TP/FP verdicts come from `azure_gpt-5.2/evaluations.json`. Note: this is 3.5.1 data, so totals diverge slightly from Phase 3.5 (TP 77→71, FP 343→385) — language-level shares shift but per-agent ratios should hold.

### Per-agent TP/FP table (456 candidates, GPT-5.2 judge, Phase 3.5.1 run)

| Agent | TP | FP | Total | Precision | % of all FPs |
|---|---:|---:|---:|---:|---:|
| correctness | 54 | 144 | 198 | **0.273** | 37.4 % |
| testing | 3 | 90 | 93 | **0.032** | 23.4 % |
| UNMATCHED | 6 | 46 | 52 | 0.115 | 11.9 % |
| security | 4 | 42 | 46 | 0.087 | 10.9 % |
| cross-file-impact | 3 | 27 | 30 | 0.100 | 7.0 % |
| consistency | 0 | 29 | 29 | **0.000** | 7.5 % |
| hallucination | 1 | 4 | 5 | 0.200 | 1.0 % |
| other (performance, historical-context) | 0 | 3 | 3 | 0.000 | 0.8 % |
| **Total** | **71** | **385** | **456** | **0.156** | 100 % |

### What the table says

- **`correctness` is the F1 engine.** 76 % of TPs come from this one agent. Do not touch.
- **`testing` is a near-pure FP generator** — 3.2 % precision, 23 % of all FPs. Its 3 TPs are: (1) `sentry-93824` Medium "time.sleep monkeypatched"; (2,3) `sentry-greptile-2` Critical/High "QuerySet negative slicing" — the second pair would also be caught by the correctness agent (same underlying bug, different file); (4) `keycloak-32918` Medium "cleanup alias literal vs computed". Sample FPs: "tests only cover failure paths" (`keycloak-37429`), "X lacks unit tests" (`keycloak-37634` x2), "JUnit args reversed" (`keycloak-37634`), "cache-miss branch untested" (`keycloak-37634`). **Classic noise pattern: generic test-coverage-ask.**
- **`consistency` is 0 % precision** — 29 candidates, zero goldens matched. Sample FPs: "POLICY_SOME_HTML should be `private static final`" (`keycloak-37429`), "missing trailing newline" (`keycloak-37429`), "package-private solely for tests" (`keycloak-37634`), "typo in config alias" (`keycloak-38446` — real but ungraded), "V2 doesn't override V1 methods" (`keycloak-36880`). **Style/naming concerns by construction; CRB goldens don't reward this category.**
- **`security` has real TPs** (4, including SSRF and an authorization-gate Critical from `ts-calcom-14740`) but 42 FPs. Disabling would lose ~5 % of TPs — do NOT disable; consider per-file dispatch gating instead.
- **`cross-file-impact`** sits at 10 % precision. 3 TPs worth keeping, 27 FPs; same trade-off shape as security.
- **`hallucination`** has tiny footprint (5 findings total, 1 TP). Not load-bearing either way.

### Napkin projections for ship-criterion-bounded cuts

Applying per-agent FP reductions to the Phase 3.5 baseline (TP=77, FP=343, R=0.566, F1=0.277). Scaling per-agent numbers from the 3.5.1 table by the ratio 343/385 ≈ 0.891:

| Lever | FP removed | TP lost | New P | New R | Napkin F1 | Δ F1 |
|---|---:|---:|---:|---:|---:|---:|
| Baseline Phase 3.5 | — | — | 0.183 | 0.566 | 0.277 | — |
| **Disable `testing` agent** | 80 | 3 | 0.220 | 0.544 | **0.313** | **+0.036** |
| **Disable `consistency` agent** | 26 | 0 | 0.195 | 0.566 | **0.290** | **+0.013** |
| **Disable `testing` + `consistency`** | 106 | 3 | 0.238 | 0.544 | **0.332** | **+0.055** |
| Disable `testing` + `consistency` + `hallucination` | 110 | 4 | 0.240 | 0.537 | 0.332 | +0.055 |
| Tighten `security` to confidence ≥ 95 (hypothetical) | ~15 | 0–1 | ~0.195 | ~0.560 | ~0.289 | ~+0.012 |

Applying IMPROVEMENTS.md's **3–5× discount** (Phase 3.5 / 3.6 / 3.7 calibrated miss ratio between projection and reality):
- `disable testing` realized: +0.007 to +0.012 → **close** band
- `disable consistency` realized: +0.003 to +0.004 → **close** band
- `disable testing + consistency` realized: +0.011 to +0.018 → **hold** band (0.288–0.295)

The calibrated projection still falls short of the 0.30+ ship floor on its own but clearly beats anything Phase 4 / 3.5.1 achieved.

### Updated lever recommendation

**Replace Candidate X's "rules/ pattern suppressor" with Candidate X.2: agent-dispatch change — disable `testing` and `consistency` by default, restorable via `.claude/soliton.local.md`.**

Why this is cleaner than the rules/ pattern approach:
- **No prose added to SKILL.md** — Phase 3.5.1's lesson was that every 40 lines of rationale prose in SKILL.md regressed non-TS F1. Agent-level toggle touches `risk-scorer.md`'s dispatch list only.
- **Reversibility** — integrations that want testing/consistency findings set the flag; default gets the F1-optimized behavior.
- **Customer value cost is bounded** — consistency agent finds only style/naming issues (zero CRB TPs, ~19 real-but-style findings per 50-PR run); testing agent finds mostly test-coverage asks (~3 real test-goldens per 50 PRs but only ~4 % of its emissions are TPs).
- **Implementation is minimal** — one edit to the default `recommendedAgents` set in `agents/risk-scorer.md`, one config key in `skills/pr-review/SKILL.md` Step 2, one line in `rules/` to document the flag.

**Pre-registered ship criteria for X.2** (tightened vs. original X since projected effect is larger):

| Outcome | Aggregate F1 | Recall | Per-language | Action |
|---|---:|---:|---|---|
| ✅ Ship | ≥ 0.30 | ≥ 0.52 | No reg > 0.03 | Replace Phase 3.5 |
| ⚠️ Hold | 0.28–0.30 | 0.50–0.52 | — | Docs PR, propose X.2.1 with security-tighten as a stacked lever |
| ❌ Close | < 0.28 | < 0.50 | Any > 0.05 | Documented negative; revert testing/consistency defaults |

### What Candidate Z was originally supposed to cost, and why Appendix A replaces it

The original Z (CRB step2 metadata-tag enrichment + re-run) would have cost ~$140 and produced per-agent data we now have for free. Savings: $140. Appendix A's data quality is slightly noisier than the originally-planned enrichment (~12 % unmatched vs. ~0 % with structured tags) but the headline per-agent table is directionally unambiguous: testing and consistency are the noise generators.

**If the operator decides the projections above aren't strong enough evidence to ship agent-disable defaults straight-to-main, the next-cheapest step is a $140 X.2 experiment under the pre-registered criteria above** — a clean A/B against Phase 3.5 with a single dispatch-list change.
