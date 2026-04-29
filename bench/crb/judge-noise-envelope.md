# Judge-noise envelope — phase5_2-reviews/ × Azure GPT-5.2

*Calibration measurement, 2026-04-29.*

**TL;DR.** Re-running the CRB judge pipeline 3 times on the existing 50-PR `phase5_2-reviews/` corpus, plus the published Phase 5.2.1 result as a 4th anchor, measures **σ_F1 aggregate = 0.0086** under the GPT-5.2 judge. Aggregate F1 across 4 independent re-runs spans 0.308 → 0.326 (range 0.018) with mean 0.321. Per-language σ tops out at **0.0179 (TS)**. Soliton's published Phase 5.2 number (0.313) was on the *low* end of the noise band; the true F1 mean on this corpus + judge is closer to 0.321.

---

## Why this measurement

Phase 5.2.1 (2026-04-21) found **Ruby F1 swung 0.029 on identical Soliton reviews** when the regex change re-stripped 5 more footnote variants. Single data point. Every per-language conclusion drawn at n=10 PRs since Phase 4c is potentially noise-dominated; this file establishes the σ band so future writeups can cite ratios against it.

`POST_V2_FOLLOWUPS.md` § A4 called for **3–5 independent judge re-runs** on the same corpus to quantify σ_F1 aggregate, σ_F1 per-language, and σ_TP per-agent. This run executes that at N=3 (plus the run0 anchor from Phase 5.2.1).

---

## Methodology

- **Held identical:** `bench/crb/phase5_2-reviews/` (50 PRs, the published Phase 5.2 Soliton output with the Phase 5.2.1 tightened-regex strip applied). Soliton-side dispatch NOT re-run.
- **Re-run:** `bench/crb/run-phase5_2-pipeline.sh` (builds `benchmark_data.json`, runs `step2_extract_comments` → `step2_5_dedup_candidates` → `step3_judge_comments`).
- **Source of variance:** GPT-5.2 (judge model) — non-deterministic at temperature 0 due to Azure OpenAI infra-side reordering / batching. Each pipeline run independently re-extracts candidates from Soliton markdown, re-deduplicates, and re-judges, so all three LLM stages contribute to the σ.
- **Snapshots:** Each run's `evaluations.json`, `candidates.json`, `dedup_groups.json` saved to `bench/crb/judge-noise-runs/run{N}/`. `analyze.txt` is the `analyze-phase5.py` output.
- **Aggregator:** `bench/crb/compute-noise-envelope.py` reads all snapshots and emits both the tables in this file and `bench/crb/judge-noise-runs/summary.json` for downstream tools.
- **run0 = published Phase 5.2.1 result** (April 21, F1=0.308) — included as the 4th anchor; same corpus, same judge, ~8 days earlier.

---

## Aggregate per-run

| run | TP | FP | FN | P | R | F1 |
|----:|---:|---:|---:|---:|---:|---:|
| run0 (published 5.2.1) | 70 | 249 | 66 | 0.219 | 0.515 | **0.308** |
| run1 | 71 | 230 | 65 | 0.236 | 0.522 | **0.325** |
| run2 | 71 | 231 | 65 | 0.235 | 0.522 | **0.324** |
| run3 | 71 | 229 | 65 | 0.237 | 0.522 | **0.326** |

| Statistic | Value |
|---|---:|
| **σ_F1 aggregate** | **0.0086** |
| Mean F1 | 0.321 |
| Min – Max | 0.308 – 0.326 |
| Range | 0.018 |
| σ_P | 0.0082 |
| σ_R | 0.0037 |
| σ_TP | 0.50 (mean 70.8) |
| σ_FP | 9.54 (mean 234.8) |
| σ_FN | 0.50 (mean 65.2) |

**Headline observation.** Variance is dominated by FP fluctuation (σ_FP ≈ 9.5) — the judge re-classifies ~10 borderline candidates differently across runs. TPs and FNs are essentially stable (σ ≤ 0.5 each, the smallest discrete unit possible given integer counts). **The judge's recall classification is reproducible; its precision classification is the noisy axis.**

run0 sits as a clear outlier (FP 249 vs. ~230 in runs 1-3). The Phase 5.2.1 published F1=0.308 measurement was at the low edge of the noise band; the *true* F1 of the Phase 5.2 footnote-strip lever on this corpus and judge is closer to **0.321 ± 0.009** (1σ).

---

## Per-language F1 (n=10 each)

| Lang | run0 | run1 | run2 | run3 | mean | σ | max swing |
|---|---:|---:|---:|---:|---:|---:|---:|
| Java | 0.275 | 0.278 | 0.275 | 0.300 | 0.282 | 0.0120 | 0.0250 |
| Python | 0.304 | 0.298 | 0.304 | 0.315 | 0.305 | 0.0069 | 0.0167 |
| Go | 0.320 | 0.343 | 0.343 | 0.310 | 0.329 | 0.0166 | 0.0330 |
| **TS** | 0.349 | 0.388 | 0.380 | 0.384 | 0.375 | **0.0179** | **0.0391** |
| Ruby | 0.283 | 0.312 | 0.312 | 0.309 | 0.304 | 0.0144 | 0.0297 |

**σ_F1 per-language max** = 0.0179 (TS). About 2.1× the aggregate σ — consistent with n=10 having ~3× the variance of n=50 by simple scaling.

**Max per-language swing across runs** = 0.0391 (TS).

**Implication for n=10 per-language writeups.** A language-specific Δ F1 of < 0.018 (1σ) is noise. < 0.036 (2σ) is provisional. Citing "Java −0.011" or "Go −0.006" as material per-language signals (as Phase 5.2 did) is overreach without re-runs — those Δs sit deep inside the per-language σ band.

---

## Per-agent TP/FP attribution (across runs)

Format: `TP/FP per run`.

| Agent | run0 | run1 | run2 | run3 | σ_TP | σ_FP |
|---|---:|---:|---:|---:|---:|---:|
| correctness | 48/113 | 46/108 | 49/107 | 48/108 | 1.26 | 2.71 |
| security | 11/49 | 11/44 | 11/47 | 10/46 | 0.50 | 2.08 |
| cross-file-impact | 2/30 | 3/29 | 2/31 | 2/29 | 0.50 | 0.96 |
| UNMATCHED | 1/26 | 3/22 | 1/21 | 2/16 | 0.96 | 4.11 |
| consistency | 2/14 | 2/12 | 2/10 | 2/14 | 0.00 | 1.91 |
| testing | 1/9 | 1/7 | 1/8 | 1/10 | 0.00 | 1.29 |
| hallucination | 1/4 | 1/4 | 1/4 | 1/4 | 0.00 | 0.00 |
| (multi-tagged) | 1/1 | 1/1 | 1/1 | 1/1 | 0.00 | 0.00 |
| observability | 1/0 | 1/1 | 1/0 | 1/0 | 0.00 | 0.50 |
| historical-context | 0/2 | 0/1 | 0/1 | 1/0 | 0.50 | 0.82 |
| performance | 0/1 | 0/1 | 0/1 | 0/1 | 0.00 | 0.00 |

**Per-agent observation.** σ_TP ≤ 1.26 across every category — the judge identifies the same TPs 95 % of the time. σ_FP is the bigger axis: `correctness` σ_FP=2.71 (~5 % of its 108 mean FPs), `UNMATCHED` σ_FP=4.11. The fuzzy-match → category-attribution pipeline is itself adding noise on the UNMATCHED bucket; that's expected, since extractor stochasticity rewords candidates differently across runs.

For per-agent ablation experiments (the Phase 5 lineage), conclusions need a TP delta ≥ 2 to clear 1σ in the largest-volume agent (correctness). All Phase 5 conclusions about `test-quality` and `consistency` (TP swings of 3 → 0 = TP delta of 3) clear that bar comfortably.

---

## Retroactive calibration of prior phase deltas

Given σ_F1 aggregate = 0.0086 (1σ); 2σ = 0.0173.

> **Two framings reported.** Ratios use `|Δ| / σ_aggregate` (single-σ — common convention; appropriate when comparing one new measurement to a fixed historical anchor). The strict standard error of the *difference* between two independent phase results is σ_Δ = √2·σ_aggregate ≈ 0.0122. The paired-σ framing is the textbook-correct test for "is Δ between two means significant"; the single-σ framing slightly overstates confidence. Both reported below; verdict drift only affects Phase 5 vs P3.5.

| Comparison | Δ F1 | \|Δ\|/σ_single | \|Δ\|/σ_Δ paired | Verdict (paired) |
|---|---:|---:|---:|:---|
| Phase 4c vs P3.5 | −0.0160 | 1.85 | 1.32 | **1–2σ provisional** |
| Phase 4c.1 vs P3.5 | +0.0010 | 0.12 | 0.08 | **< 1σ noise** |
| Phase 3.5.1 vs P3.5 | −0.0340 | 3.93 | 2.80 | **> 2σ signal** |
| Phase 5 vs P3.5 | +0.0230 | 2.66 | 1.89 | **1–2σ provisional** ← drifts from signal under paired |
| Phase 5.2 vs P3.5 | +0.0360 | 4.16 | 2.96 | **> 2σ signal** |
| Phase 5.2 vs P5 | +0.0130 | 1.50 | 1.07 | **1–2σ provisional** |
| Phase 5.2.1 re-run vs P5.2 | −0.0050 | 0.58 | 0.41 | **< 1σ noise** |

### Implications retroactively applied to prior writeups

1. **Phase 5 + Phase 5.2 stack vs Phase 3.5 (+0.036) is a real signal** (4.16σ_single, 2.96σ_Δ paired). The cumulative jump from 0.277 to 0.313/0.321 is not noise under either framing. The published narrative holds. **Phase 5 alone (+0.023) drops from "signal" to "provisional" under the paired framing** — defensible at 1.89σ_Δ given the run0 anchor sits at the LOW edge of its noise band, but the strongest claim is the cumulative 5+5.2 stack, not Phase 5 in isolation.

2. **Phase 5.2's +0.013 lift over Phase 5 (footnote strip alone) is provisional at 1.5σ.** The lever might be real, might be partly judge variance — within the noise band, indistinguishable. Saying "+0.013 from footnote strip" is overclaiming; saying "+0.013 within ±0.009 noise" is honest. The prior writeup acknowledged this with the Phase 5.2.1 re-run; this calibration confirms the caution was warranted.

3. **Phase 4c's −0.016 regression was correctly held as "close" not "ship".** 1.85σ — borderline. Pre-reg discipline kept us from over-interpreting it as either signal or noise. The same regression measured under N=4 noise discipline could have gone either way; the current measurement supports the original pre-reg verdict.

4. **Phase 4c.1's "neutral, slight regression" claim is now downgradeable to pure noise.** 0.12σ — within run-to-run variance. The conclusion that "isolated 4a is neutral" is fully supported, perhaps even understated; there's no actionable signal in the 4a-alone data at this corpus size and N=1 judge run.

5. **Phase 3.5.1's −0.034 regression is firm signal at ~4σ.** The Phase 3.5.1 close verdict (and the prose-verbosity hypothesis for why TS-only nitpicks bled across all languages) is well-supported.

6. **Phase 5.2 vs Phase 5.2.1 re-run difference (Δ −0.005) was correctly classified as judge noise** in the prior writeup. 0.58σ. Confirmed.

### Per-language writeups need narrower claims

Phase 5.2's per-language headline ("TS +0.076, Python +0.074, Ruby +0.022, Java −0.011, Go −0.006 vs Phase 3.5") is mostly real for TS/Python (those Δs clear 4σ on the per-language σ band of 0.018), provisional for Ruby (1.2σ), and **noise-only for Java and Go** (both within 1σ of zero). Future per-language writeups should cite |Δ_lang| / σ_lang ratios (or σ_aggregate × √5 ≈ 0.019 as a conservative per-language band) rather than raw Δs.

---

## Reproduction

```bash
# Pre-req: ../code-review-benchmark/offline checked out and uv-installable.
# Azure CLI logged in via `az login`.
# Sibling llm_client.py patched to AzureCliCredential(process_timeout=60) — see § Auth note.

mkdir -p bench/crb/judge-noise-runs/run{1,2,3}
for i in 1 2 3; do
  PYTHONUNBUFFERED=1 bash bench/crb/run-phase5_2-pipeline.sh \
    2>&1 | tee bench/crb/judge-noise-runs/run${i}/pipeline.log
  cp ../code-review-benchmark/offline/results/azure_gpt-5.2/{evaluations,candidates,dedup_groups}.json \
     bench/crb/judge-noise-runs/run${i}/
  PYTHONUTF8=1 python3 bench/crb/analyze-phase5.py \
    > bench/crb/judge-noise-runs/run${i}/analyze.txt 2>&1
done
PYTHONUTF8=1 python3 bench/crb/compute-noise-envelope.py
```

### Auth note (Windows + az.cmd, 2026-04-29 finding)

Sibling `code-review-benchmark/offline/code_review_benchmark/llm_client.py` was patched locally on 2026-04-29 from:

```python
DefaultAzureCredential()
```

to:

```python
AzureCliCredential(process_timeout=60)
```

with a `AZURE_CRB_USE_DEFAULT_CRED` env var to opt back into the original chain.

Without the patch, `step2_extract_comments`'s parallel asyncio.gather hangs ~2.5 min per call cycling through every credential type (EnvironmentCredential, WorkloadIdentityCredential, ManagedIdentityCredential, SharedTokenCacheCredential, VisualStudioCodeCredential, AzureCliCredential, AzurePowerShellCredential, AzureDeveloperCliCredential, BrokerCredential) before failing on each of the 50 parallel calls. Observed result: pipeline appeared "stuck" for 30+ min at 0 % progress.

Root cause: AzureCliCredential's default `process_timeout=10` is too short for Windows `az.cmd` cold-start, which takes ~15 sec on this dev machine. Bumping to 60 sec fixes it.

**Patch is local, not committed upstream.** A future PR to `withmartian/code-review-benchmark` could submit the same fix as a Windows-compatibility improvement.

---

## Cost

| Activity | Cost |
|---|---:|
| Run #1 judge pipeline | ~$15 |
| Run #2 judge pipeline | ~$15 |
| Run #3 judge pipeline | ~$15 |
| **Total** | **~$45** |

(Pre-authorized in session prompt; calibration spend ladder allowed extension to 5 runs / ~$75 if σ tight at N=3 or noisy enough to warrant more samples — not exercised, capped at 3 because σ aggregate landed at 0.0086 with run1/run2/run3 clustered tightly within ±0.001 of each other.)

---

## Recommended doctrine going forward

1. **Single CRB number reporting.** Prefer reporting "F1 = X ± σ over N independent judge re-runs" rather than a single point estimate. Ship-criteria specs should be written against the mean and require a 2σ separation from baseline before declaring signal.

2. **Per-language signals require either ≥ 2σ (≈ 0.036 at n=10) or N ≥ 3 judge re-runs.** Single-run per-language deltas of < 0.036 should be reported with a "may be noise" qualifier.

3. **Per-agent ablation experiments are fine at N=1.** σ_TP for the largest agent (correctness) is 1.26; ablations producing TP deltas ≥ 3 (as Phase 5's `test-quality`/`consistency` removal did) are well above this floor.

4. **Footnote-strip levers and similar mechanism fixes need re-runs.** Phase 5.2's +0.013 isolated lift sits at 1.5σ — the next time a similar zero-TP-cost lever is proposed, run N=3 before claiming a CRB-leaderboard-relevant move.

---

*Filed under: Soliton / CRB / methodology. Replaces the placeholder σ ≈ 0.02 in `idea-stage/POST_V2_FOLLOWUPS.md` § F.1.*
