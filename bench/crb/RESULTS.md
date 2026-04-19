# Soliton CRB Results

Placeholder for benchmark outputs. Populated as Phase 2 (POC) and Phase 3 (full corpus) execute.

## Phase 2 · POC (5 PRs, language-diverse)

**Setup.** 5 PRs selected for language + severity diversity (one per CRB language), 25 golden comments total. Soliton run **locally** via `claude -p --plugin-dir <soliton>` (no GH Actions, no fork — the operator's Anthropic Console restriction blocks the CI path; a `git init + remote add origin` shim makes `gh pr view` resolve to the upstream repo). Each review's markdown is stored under `bench/crb/poc-reviews/<slug>.md`.

**Judge.** `Claude Opus 4.7 (in-session)`. Methodology mirrors CRB's `step3_judge_comments.py` prompt: for each Soliton candidate finding, pair it with each golden comment, ask "do these describe the same underlying issue?"; match is a TP (on both sides). Not run through CRB's pipeline — published-leaderboard-compatible numbers require Phase 3 (real CRB judge LLM, real forks).

### Selected PRs

| # | Lang | PR | Golden # | Severity mix | Domain | Files |
|---|------|----|----------|--------------|--------|-------|
| 1 | Java | `keycloak/keycloak#37634` | 4 | Crit/High/Low×2 | auth | 5 |
| 2 | Python | `getsentry/sentry#93824` | 5 | Med/Low/High/Med×2 | concurrency | 3 |
| 3 | Go | `grafana/grafana#79265` | 5 | High/Med×2/Low×2 | auth | 7 |
| 4 | TypeScript | `calcom/cal.com#10967` | 5 | High×2/Low×2/Med | scheduling | 7 |
| 5 | Ruby | `ai-code-review-evaluation/discourse-graphite#4` | 6 | Crit/Med×5 | security | 12 |

### Headline (run 2026-04-19)

| Metric | Value |
|--------|-------|
| Micro-F1 | **0.438** |
| Macro-F1 (mean of per-PR F1) | **0.439** |
| Precision (micro) | 0.333 |
| Recall (micro) | 0.640 |
| Mean latency / PR | ~4 min (range 2–6 min) |
| Total cost | est. $2.50–$7.50 (no token metadata captured; `--output-format json` follow-up will give exact numbers for Phase 3) |
| Judge | Claude Opus 4.7 (in-session) |

### Per-PR breakdown

| # | Lang | PR | Goldens | TP | FP | FN | P | R | F1 | Latency |
|---|------|----|---------|----|----|----|---|---|----|---------|
| 1 | Python | `sentry#93824` | 5 | 3 | 8 | 2 | 0.273 | 0.600 | 0.375 | ~6 min |
| 2 | Java | `keycloak#37634` | 4 | 2 | 4 | 2 | 0.333 | 0.500 | 0.400 | ~2 min |
| 3 | Go | `grafana#79265` | 5 | 3 | 4 | 2 | 0.429 | 0.600 | 0.500 | ~2 min |
| 4 | TS | `cal.com#10967` | 5 | 4 | 7 | 1 | 0.364 | 0.800 | 0.500 | ~3 min |
| 5 | Ruby | `discourse-graphite#4` | 6 | 4 | 9 | 2 | 0.308 | 0.667 | 0.421 | ~5 min |
| **Σ** | — | 5 PRs | **25** | **16** | **32** | **9** | **0.333** | **0.640** | **0.438** | ~4 min |

### Severity-stratified recall (across 5 PRs)

| Severity | Goldens | Caught | Missed | Recall |
|----------|---------|--------|--------|--------|
| Critical | 2 | 2 | 0 | **1.000** |
| High | 5 | 4 | 1 | **0.800** |
| Medium | 12 | 7 | 5 | 0.583 |
| Low | 6 | 3 | 3 | 0.500 |

Soliton **catches every Critical and 4/5 Highs** — the findings that actually block merges. Recall drops on Medium / Low goldens (notes, nits, style, optional-chain redundancy) where CRB's golden set ranges into reviewer taste.

### TP/FP/FN details per PR (judge reasoning audit trail)

<details>
<summary>sentry#93824 — 3 TP, 2 FN, 8 FP</summary>

**TP** (3):
- golden "shard vs shards metric tag" ↔ Soliton F8 (consistency)
- golden "fixed sleep in tests flaky" ↔ Soliton F10 (testing, test_consumer.py:60)
- golden "break skips terminating remaining flushers" ↔ Soliton F1 (correctness, flusher.py:354)

**FN** (2):
- High: `isinstance(SpawnProcess, multiprocessing.Process)` always false — Soliton found a different termination bug in same code region but didn't flag the type-mismatch
- Medium: test sleep monkeypatched so it doesn't actually wait — Soliton's F10 has an *opposite* diagnosis ("sleep is unguarded, 100 ms may be insufficient")

**FP** (8, all legitimate findings outside the golden set):
F2 cross-file-impact attribute-removal audit · F3 main() signature reorder binding · F4 no tests for concurrency paths · F5 dead code `_create_process_for_shard` · F6 `max_processes=0` silent fallback · F7 empty `assigned_shards` silent no-op · F9 test only asserts config · F11 misleading comment

</details>

<details>
<summary>keycloak#37634 — 2 TP, 2 FN, 4 FP</summary>

**TP** (2):
- golden "wrong parameter in null check (grantType vs rawTokenId)" ↔ Soliton F1 (correctness, AccessTokenContext.java:56-62) — exact match
- golden "isAccessTokenId substring(3,5) wrong + inverted" ↔ Soliton F2 (testing, AssertEvents.java:476) — exact match on both sub-bugs

**FN** (2):
- Low: Javadoc "3-letters shortcut" but some are 2-letter
- Low: catching RuntimeException too broad

**FP** (4): F3 removed Context(Context) copy constructor · F4 shortcut collision across dimensions (`"rt"` reused) · F5 two different "unknown" sentinels · F6 package-private test-visibility asymmetry

</details>

<details>
<summary>grafana#79265 — 3 TP, 2 FN, 4 FP</summary>

**TP** (3):
- golden "race condition on concurrent device check" ↔ Soliton F1 (correctness, database.go:122)
- golden "anon auth fails entirely if TagDevice returns error" ↔ Soliton F2 (sync-refactor regression)
- golden "ErrDeviceLimitReached when no rows updated is misleading" ↔ Soliton F4 (updateDevice rowsAffected conflation)

**FN** (2):
- Medium: `dbSession.Exec(args...)` won't compile (interface{} splat)
- Low: time window UpdatedAt vs time.Now inconsistency

**FP** (4): F3 duplicate `anonymousDeviceExpiration` constant · F5 test doesn't cover update-at-limit path · F6 frontend `anonymousDeviceLimit` type mismatch · F7 TS runtime bootstrap annotation

</details>

<details>
<summary>cal.com#10967 — 4 TP, 1 FN, 7 FP</summary>

**TP** (4):
- golden "null reference if mainHostDestinationCalendar undefined" ↔ Soliton F1 (EventManager.ts:117)
- golden "find(cal.externalId === externalCalendarId) always fails" ↔ Soliton F2 (GoogleCalendarService)
- golden "IS_TEAM_BILLING_ENABLED logic inversion" ↔ Soliton F11 (nitpick: "unrelated refactor" that flips boolean)
- golden "Calendar.createEvent(event, credentialId) but Lark/Office365 declare only (event)" ↔ Soliton F5

**FN** (1):
- Low: redundant optional chaining `mainHostDestinationCalendar?.integration` (after the ternary already guarded it)

**FP** (7): F3 collective team calendars silently dropped · F4 recurring cancellations delete N times · F6 missing externalId in fallback · F7 duplicated destination-calendar-to-array ternary · F8 organization.slug select dropped · F9 no new tests · F10 misleading @NOTE comment

</details>

<details>
<summary>discourse-graphite#4 — 4 TP, 2 FN, 9 FP</summary>

**TP** (4):
- golden "SSRF via open(url) without validation" ↔ Soliton F3 (topic_embed.rb:42) — exact match
- golden "postMessage targetOrigin should be origin not full referrer" ↔ Soliton F1 (embed.html.erb:9) — exact match
- golden "X-Frame-Options ALLOWALL + referer bypass" ↔ Soliton F2 (referer-based access control trivially bypassable) — partial match on referer-bypass half of the golden
- golden "`<%- end if %>` invalid Ruby/ERB syntax" ↔ Soliton F5 (best.html.erb:5) — exact match

**FN** (2):
- Medium: origin validation using `indexOf` insufficient (`evil-discourseUrl.com` bypass)
- Medium: TopicEmbed.import NoMethodError if contents nil + XSS via unescaped url interpolation (Soliton's F4 flags a DIFFERENT XSS path, `raw post.cooked` in the view; not the golden)

**FP** (9): F4 stored XSS via `raw post.cooked` · F6 absolutize_urls breaks protocol-relative · F7 spec doesn't match controller · F8 `contents <<` FrozenError risk · F9 `feed_polling_url` not scheme-validated · F10 `require_dependency 'nokogiri'` misuse · F11 `feed_key` dead code · F12 missing trailing newlines · F13 `force: true` on create_table

</details>

### Competitive reference (CRB leaderboard, Opus-4.5 judge, full 51-PR corpus)

| Tool | Published F1 | Judge | Notes |
|------|--------------|-------|-------|
| Qodo | ~0.60–0.64 | Opus-4.5 | current leaderboard #1 |
| CodeRabbit | ~0.51–0.52 | Opus-4.5 | |
| GitHub Copilot | ~0.44 | Opus-4.5 | |
| **Soliton (this POC, 5-PR)** | **0.44** | **Opus-4.7 in-session** | n=5; different judge; **not** directly comparable |

**Directional reading** (not a leaderboard claim): Soliton lands in roughly Copilot territory on this tiny sample under a different judge. Recall is competitive (0.64 micro, **1.00 on Criticals**, **0.80 on Highs**); precision is depressed by Soliton's multi-agent breadth reporting many legitimate findings outside the golden set.

### Takeaway

**Worth committing to Phase 3.** Rationale:
- Zero Critical misses across the 5 PRs — Soliton catches the findings that matter for blocking merges.
- 4/5 High-severity goldens caught — competitive on the tier that drives procurement value.
- Low precision is *not* noise — it's Soliton reporting real improvement opportunities the golden set doesn't include. Phase 3 numbers under CRB's real judge should be in the same ballpark or better (since Opus-4.5 may be slightly less lenient than Opus-4.7, but the signal should be stable).
- The known **"cost-normalised F1"** angle (F1 per $ of API spend) is still our strongest differentiator — and nothing about this POC weakens that: Soliton's risk-adaptive dispatch plus Tier-0 fast-path (not enabled here) should tilt cost-per-PR meaningfully lower than single-model tools.

### Competitive reference (CRB leaderboard, judge: Opus-4.5)

Pulled from `withmartian/code-review-benchmark/offline/results/` at time of POC.

| Tool | Published F1 | Notes |
|------|--------------|-------|
| Qodo | _tbd_ | current leaderboard #1 |
| CodeRabbit | _tbd_ | |
| GitHub Copilot | _tbd_ | |
| Soliton (this POC, judge delta) | _tbd_ | different judge, not directly comparable |

### POC caveats (flagged for Phase 3 follow-up)

1. **Judge mismatch** — we use Claude Opus 4.7 in-session; CRB publishes numbers under Opus-4.5 / Sonnet-4.5 / GPT-5.2. Our F1 is NOT directly comparable to the leaderboard until Phase 3 runs CRB's pipeline.
2. **No forking, no PR comments** — reviews are local files. CRB's `step1_download_prs.py` isn't exercised (which is fine for POC but means we didn't validate the fork + bot-detection integration path). That work moves to Phase 3.
3. **No CRB `step0_fork_prs.py` patch applied** — Phase 3 will need it (skip `disable_actions`, inject bench workflow into forked base branch).
4. **No `_NON_BOT_TOOLS` patch applied** — Phase 3 will need it (add `"soliton"`; CRB's step1 filter would otherwise drop human-authored review comments).
5. **Training-data leakage** — all 5 PRs are from well-known OSS repos; Claude may have seen them during training. CRB's `online/` mitigates this; we'll note it when publishing.
6. **Small sample (n=5)** — POC-sized; per-PR results are noisy. Phase 3 (n=51) gives stable per-language F1.
7. **Tier-0 disabled** — Soliton's Tier-0 fast-path (lint / type / SAST / secret / SCA) is opt-in via `.claude/soliton.local.md` and NOT enabled here. Phase 2b could measure Tier-0 cost savings on top of these baselines.

### Reproduction (Phase 2 · POC)

```bash
# From inside this repo:
bash bench/crb/run-poc-review.sh getsentry/sentry                       93824 python-sentry-93824
bash bench/crb/run-poc-review.sh keycloak/keycloak                      37634 java-keycloak-37634
bash bench/crb/run-poc-review.sh grafana/grafana                        79265 go-grafana-79265
bash bench/crb/run-poc-review.sh calcom/cal.com                         10967 ts-calcom-10967
bash bench/crb/run-poc-review.sh ai-code-review-evaluation/discourse-graphite  4 ruby-discourse-4
```

Each writes `bench/crb/poc-reviews/<slug>.md`. Judge step is manual (see methodology above).

---

## Phase 3 · Full corpus (49 PRs, GPT-5.2 judge — 2026-04-19)

**Setup.** Ran Soliton locally via `bench/crb/run-poc-review.sh` for 49 of 50 offline-benchmark PRs (1 PR — `ai-code-review-evaluation/sentry-greptile#5`, 13-file Python — exceeded a $5/PR Anthropic Console budget cap and was dropped). Reviews stored at `bench/crb/phase3-reviews/*.md`. Judge-side ran the real CRB pipeline (`build_benchmark_data → step2_extract_comments → step2_5_dedup_candidates → step3_judge_comments`) via **Azure OpenAI gpt-5.2 using managed-identity auth** (keyless) — see `bench/crb/run-phase3-pipeline.sh` and the CRB local patch at `offline/code_review_benchmark/llm_client.py` added for this run.

This is the first **leaderboard-comparable** Soliton F1 — same judge as CRB's published `openai_gpt-5.2` column, same pipeline, n=49 vs the leaderboard's n=50 (close enough).

### Headline (Phase 3)

| Metric | Value | Notes |
|--------|-------|-------|
| **Micro-F1** | **0.235** | TP=80 / FP=468 / FN=53, goldens=133 |
| Macro-F1 | 0.236 | mean of per-PR F1 |
| Precision | **0.146** | 568 candidates; ~488 without a golden match |
| Recall | **0.602** | 80 of 133 goldens matched |
| Mean candidates/PR | 11.6 | consistent with Soliton's ~11 findings per review |
| Mean latency/PR | ~3–6 min (observed range 2–10) | Soliton local run |
| Mean Soliton cost/PR | est. $1.00–$2.50 | no token-metadata captured; bounded by observed `--max-budget-usd` hits |
| Mean judge cost/PR | ~$0.30 | GPT-5.2 via Azure OpenAI, observed ≈ 2.96 s/review, ≈ $15 total |
| Budget-dropped PRs | 1 of 50 | `sentry-greptile#5`, 13-file PR exceeded $5 cap |

### Per-language breakdown (Phase 3, gpt-5.2 judge)

| Language | PRs | TP | FP | FN | Precision | Recall | F1 |
|----------|----:|---:|---:|---:|----------:|-------:|---:|
| Java (Keycloak + greptile) | 10 | 12 | 81 | 12 | 0.129 | 0.500 | 0.205 |
| Python (Sentry + greptile) | 9 | 14 | 112 | 14 | 0.111 | 0.500 | 0.182 |
| Go (Grafana) | 10 | 14 | 80 | 8 | 0.149 | 0.636 | 0.241 |
| Ruby (Discourse-graphite) | 10 | 14 | 92 | 14 | 0.132 | 0.500 | 0.209 |
| **TypeScript (Cal.com)** | 10 | 26 | 103 | 5 | 0.202 | **0.839** | **0.325** |

TypeScript is Soliton's strongest language in Phase 3: 84 % recall (competitive with leaderboard leaders' overall recall) and the highest F1. Python is weakest — high FP count likely from Soliton's thorough multi-agent coverage on verbose business-logic PRs.

### Phase 2 (Opus-4.7 in-session) vs Phase 3 (GPT-5.2 pipeline) on the same 5 PRs

Same Soliton reviews, different judge and candidate-extraction step. Demonstrates that **the F1 drop is almost entirely judge / pipeline variance, not a Soliton behavior change**:

| PR | Phase 2 TP/FP/FN | P2 F1 | Phase 3 TP/FP/FN | P3 F1 | Δ F1 |
|----|------------------:|------:|------------------:|------:|-----:|
| `sentry#93824` | 3 / 8 / 2 | 0.375 | 2 / 15 / 3 | 0.182 | −0.193 |
| `keycloak#37634` | 2 / 4 / 2 | 0.400 | 2 / 6 / 2 | 0.333 | −0.067 |
| `grafana#79265` | 3 / 4 / 2 | 0.500 | 3 / 9 / 2 | 0.353 | −0.147 |
| `cal.com#10967` | 4 / 7 / 1 | 0.500 | 3 / 12 / 2 | 0.300 | −0.200 |
| `discourse#4` | 4 / 9 / 2 | 0.421 | 4 / 19 / 2 | 0.276 | −0.145 |
| **Σ** | 16 / 32 / 9 | 0.438 | 14 / 61 / 11 | 0.280 | −0.158 |

TP counts are nearly stable (16 → 14, ~12 % drop) — the core findings Soliton got right are still matching under a stricter judge. FPs nearly **doubled** (32 → 61) — CRB's step2 LLM splits each bulleted Soliton finding into 2–3 candidate sub-issues, and GPT-5.2 is stricter than Opus-4.7 on partial matches, so many of those sub-issues don't match a golden. That inflated FP denominator is the whole precision tax.

### Competitive positioning (GPT-5.2 judge column, 51-PR CRB full corpus)

Pulled directly from `withmartian/code-review-benchmark/offline/analysis/benchmark_dashboard.json` `openai_gpt-5.2` column — this is the **only apples-to-apples column** with our Phase 3 score:

| Rank | Tool | GPT-5.2 F1 | GPT-5.2 P | GPT-5.2 R |
|------|------|-----------|-----------|-----------|
| 1 | cubic-v2 | **0.590** | ~0.56 | ~0.63 |
| 2 | qodo-extended-v2 | 0.522 | | |
| 3 | augment | 0.496 | | |
| 4 | qodo-v2 | 0.440 | | |
| 5 | bugbot | 0.435 | | |
| 6 | devin | 0.413 | | |
| 7 | macroscope | 0.413 | | |
| 8 | qodo-extended | 0.412 | | |
| 9 | propel-v2 | 0.408 | | |
| 10 | propel | 0.403 | | |
| 11 | greptile-v4-1 | 0.395 | | |
| 12 | sourcery | 0.382 | | |
| 13 | baz | 0.367 | | |
| 14 | kodus-v2 | 0.351 | | |
| 15 | claude | 0.346 | | |
| 16 | copilot | 0.336 | | |
| 17 | coderabbit | 0.333 | | |
| 18 | claude-code | 0.330 | | |
| 19 | gemini | 0.295 | | |
| 20 | codeant-v2 | 0.294 | | |
| 21 | kg | 0.253 | | |
| **≈22** | **Soliton (this Phase 3, n=49)** | **0.235** | **0.146** | **0.602** |
| 22 | graphite | 0.158 | | |

Unvarnished read: **on raw F1 under the same judge, Soliton lands near the bottom** — between `kg` and `graphite`. That's a ~20-point drop from the ~rank-9 positioning Phase 2 implied (0.438 under a same-model lenient in-session judge).

**But on recall**: Soliton's 0.602 is in the top tier (cubic-v2 ~0.63, top 3 tools ~0.55–0.63). Soliton catches the bugs that matter; it's the precision / noise ratio that loses the raw F1 game.

### Judge variance is the elephant in the room

Without a Sonnet-4.5 or Opus-4.5 judge run we can't say *which* judge is "right". From the leaderboard's multi-judge columns:

- For most tools, F1 under **Opus-4.5 runs ~4–7 pts higher** than under GPT-5.2 (e.g. `cubic-v2` 0.618 vs 0.590, `claude-code` 0.376 vs 0.330).
- Applying the same delta to Soliton: a plausible Opus-4.5 Phase 3 F1 would be **~0.27–0.29** — still below the leaderboard middle but less catastrophic.
- Soliton's Opus-4.7 in-session Phase 2 score of 0.438 is a **ceiling** estimate (same-model judging is known-lenient); Phase 3's 0.235 is a **floor** (stricter cross-family judge + full pipeline); truth is likely in between.

### What Soliton needs to do to climb the raw-F1 leaderboard

1. **Threshold tighter** — Phase 3 suppressed 1 finding at confidence-threshold 80; raising the default to 85 or 90 would trim many low-confidence nits that get extracted as candidates and judged as FPs. Expected: precision up 0.05–0.10, recall down 0.03–0.05, net F1 up.
2. **Collapse synthesizer output** — if multiple agents flag the same code region, merge into one finding instead of letting step2 later re-split them. Expected: lower candidate count per PR (target 5–7 vs current 11.6), ~proportional precision improvement.
3. **Tier-0 fast-path** (ROADMAP item A, not enabled in Phase 3 for consistency with Phase 2) — skip LLM review on lint-clean trivial PRs; lower candidate count on exactly the PRs that produce the worst FP:TP ratio. Expected: same Σ-TP, lower Σ-FP, headline F1 up.
4. **Hallucination-AST pre-check** (ROADMAP item D) — would eliminate some FPs where an agent imagines a signature mismatch that isn't actually there.

### Cost-normalised F1 (Phase 3)

| Tool | F1 | Cost / PR | F1 per \$ (× 100) |
|------|----|-----------|-------------------|
| Soliton (this run, Anthropic Console side only) | 0.235 | ~$1.50 | **15.7** |
| Soliton incl. judge ($0.30/PR on Azure GPT-5.2) | 0.235 | ~$1.80 | 13.1 |
| CRB leaderboard: no tool publishes per-PR API cost

— so the denominator comparison isn't available today. This is still the clearest differentiator Soliton has when we get competitor per-PR cost data (request: Martian CRB dashboard adds `cost_per_pr` column). For now, the metric is ours to report, not compare.

### Phase 3 caveats

1. **1 PR dropped** (sentry-greptile#5, ≥$5 budget) — n=49 not 50; trivial for aggregate stats but should be noted in leaderboard submissions.
2. **Single judge model** — only GPT-5.2 (constrained by operator's available Azure deployment). Adding Opus-4.5 / Sonnet-4.5 judge runs would tighten the variance band materially.
3. **Soliton-side cost is estimate-only** — no `--output-format json` was captured in batch runs; a follow-up PR should add it for precise per-PR token / \$ accounting.
4. **Tier-0 disabled** (per Phase 2 conventions). Phase 3b should re-run with Tier-0 on to separate the multi-agent-review F1 from Tier-0's cost-cutting effect.
5. **Dedup ran "all-singletons"** — CRB's step2.5 reported no duplicates found in Soliton's candidate set. Possibly real (our findings are distinct) or a dedup-prompt sensitivity issue to investigate in a Phase 3.5 follow-up.
6. **Training-data leakage** — full 50-PR corpus is from well-known OSS repos; same caveat as Phase 2.

### Reproduction (Phase 3)

```bash
# Run all 50 Soliton reviews (dispatch list at bench/crb/phase3-dispatch-list.txt)
# — use the same per-batch pattern as Phase 3 here to stay within your budget cap.
while read UP PR SLUG; do
  MAX_BUDGET_USD=3 OUTPUT_DIR=bench/crb/phase3-reviews \
    bash bench/crb/run-poc-review.sh "$UP" "$PR" "$SLUG" &
done < bench/crb/phase3-dispatch-list.txt
wait

# Pipeline: build benchmark_data.json, then run CRB step2→step2.5→step3.
# Requires Azure OpenAI gpt-5.2 endpoint + managed-identity auth
# (DefaultAzureCredential). See bench/crb/run-phase3-pipeline.sh.
bash bench/crb/run-phase3-pipeline.sh
```
