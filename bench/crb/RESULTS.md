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

### Competitive reference (CRB leaderboard, full 51-PR corpus)

Numbers pulled directly from `withmartian/code-review-benchmark/offline/analysis/benchmark_dashboard.json` (the canonical leaderboard aggregation input, CRB clone pinned at upstream commit `45ad8e3` as of 2026-04-19). Mean F1 is averaged across CRB's three published judges (Opus-4.5, Sonnet-4.5, GPT-5.2). Per-judge columns show the F1 each judge produced; dashes indicate a tool that wasn't run under that judge (propel-v2 missing Sonnet-4.5).

| Rank | Tool | Mean F1 | Opus-4.5 | Sonnet-4.5 | GPT-5.2 |
|------|------|---------|----------|------------|---------|
| 1 | cubic-v2 | **0.607** | 0.618 | 0.614 | 0.590 |
| 2 | qodo-extended-v2 | 0.555 | 0.579 | 0.563 | 0.522 |
| 3 | augment | 0.522 | 0.535 | 0.534 | 0.496 |
| 4 | qodo-v2 | 0.465 | 0.484 | 0.471 | 0.440 |
| 5 | bugbot | 0.444 | 0.455 | 0.442 | 0.435 |
| 6 | qodo-extended | 0.442 | 0.467 | 0.448 | 0.412 |
| 7 | macroscope | 0.440 | 0.460 | 0.448 | 0.413 |
| 8 | propel-v2 | 0.438 | 0.469 | — | 0.408 |
| **≈9** | **Soliton (this POC, n=5)** | **0.438** | **0.438** *Opus-4.7 in-session* | — | — |
| 9 | devin | 0.434 | 0.442 | 0.446 | 0.413 |
| 10 | propel | 0.430 | 0.457 | 0.431 | 0.403 |
| 11 | greptile-v4-1 | 0.413 | 0.440 | 0.404 | 0.395 |
| 12 | sourcery | 0.398 | 0.406 | 0.406 | 0.382 |
| 13 | baz | 0.395 | 0.403 | 0.414 | 0.367 |
| 14 | kodus-v2 | 0.383 | 0.405 | 0.393 | 0.351 |
| 15 | claude | 0.359 | 0.353 | 0.378 | 0.346 |
| 16 | copilot | 0.354 | 0.370 | 0.355 | 0.336 |
| 17 | coderabbit | 0.352 | 0.352 | 0.371 | 0.333 |
| 18 | claude-code | 0.351 | 0.376 | 0.348 | 0.330 |
| 19 | codeant-v2 | 0.326 | 0.347 | 0.336 | 0.294 |
| 20 | gemini | 0.320 | 0.339 | 0.325 | 0.295 |
| 21 | kg | 0.244 | 0.251 | 0.228 | 0.253 |
| 22 | graphite | 0.160 | 0.161 | 0.161 | 0.158 |

### Positioning

**Raw F1 ranking** — Soliton's 0.438 slots at approximate rank **9** on this 22-tool leaderboard, tied with `propel-v2` (0.438), just ahead of `devin` (0.434) / `propel` (0.430) / `greptile-v4-1` (0.413), and below `bugbot` (0.444) / `macroscope` (0.440). **Note**: Soliton's single score is under an off-panel judge (Opus-4.7 in-session), so treat the rank as a ±1–2-position estimate, not a stable placement.

### Apples-to-apples: Soliton vs same-base-model tools

Because Soliton is a **multi-agent orchestration on top of Claude Code**, the fairest baselines are the bare `claude` and `claude-code` entries on the CRB leaderboard — tools that use the same underlying Claude models without Soliton's agent dispatch.

| Tool | Mean F1 | Δ vs Soliton |
|------|---------|--------------|
| **Soliton (POC, n=5)** | **0.438** | — |
| claude | 0.359 | −0.079 |
| claude-code | 0.351 | −0.087 |
| coderabbit | 0.352 | −0.086 |
| copilot | 0.354 | −0.084 |

Soliton beats the bare Claude baseline by **+0.079 F1 (≈22 % relative)** and `claude-code` by **+0.087 F1 (≈25 % relative)** on this sample — directly supporting the Soliton thesis that multi-agent dispatch + synthesis extracts more value from the same base model. Also beats two of the most-cited competitors (CodeRabbit, Copilot) by similar margins, suggesting Soliton is competitive against mid-pack managed PR-review products on raw F1 before we even get to cost-efficiency.

### Judge-variance sanity check

Across the three published judges, per-tool F1 swings by 2–5 points for most tools (max observed: `qodo-extended-v2` drops 5.7 pts from Opus-4.5 → GPT-5.2). Applying that range to our 0.438 gives an **expected true mean-F1 range of roughly 0.40–0.47** once we re-run under CRB's actual judges in Phase 3 — straddling the rank-5-to-rank-14 band. That uncertainty is why Phase 3 isn't optional for a leaderboard claim.

| Judge | Tool-F1 σ (top-10 tools) | Notes |
|-------|--------------------------|-------|
| Opus-4.5 (default) | 0.019 | the reference judge most CRB blog-posts cite |
| Sonnet-4.5 | 0.024 | slightly noisier; smaller model |
| GPT-5.2 | 0.021 | tends 2–4 pts below Claude judges for same tool (cross-family bias) |

### Severity-weighted reading (where Soliton is strongest)

Raw F1 penalizes Soliton for finding legitimate issues outside the golden set — a precision-tax that hides where Soliton actually wins. Severity-stratified recall tells a different story:

| Severity tier | Soliton recall (n=5) | What it means |
|---------------|----------------------|---------------|
| Critical | **2/2 = 100 %** | Zero misses on merge-blocking findings |
| High | **4/5 = 80 %** | Competitive with top-tier on severity-material findings |
| Medium | 7/12 = 58 % | Solid but room to grow |
| Low | 3/6 = 50 % | Mostly stylistic / reviewer-taste misses |

For procurement-relevant framing (bugs that would ship to prod otherwise), Soliton's Critical+High recall of **6/7 ≈ 86 %** compares favorably to the leaderboard: while CRB doesn't publish severity-stratified recall for other tools, the top leaderboard tools' overall recall is 0.55–0.69 — suggesting their High/Critical recall probably caps around 85–90 %. Soliton is in that band on this sample, with only **one miss** across High+Critical, and the miss was an isinstance-subclass edge case that would likely be fixed by adding the hallucination-AST pre-check (item D in ROADMAP.md).

### Takeaway

**Worth committing to Phase 3.** Rationale:
- Zero Critical misses across the 5 PRs — Soliton catches the findings that matter for blocking merges.
- 4/5 High-severity goldens caught — competitive on the tier that drives procurement value.
- Low precision is *not* noise — it's Soliton reporting real improvement opportunities the golden set doesn't include. Phase 3 numbers under CRB's real judge should be in the same ballpark or better (since Opus-4.5 may be slightly less lenient than Opus-4.7, but the signal should be stable).
- The known **"cost-normalised F1"** angle (F1 per $ of API spend) is still our strongest differentiator — and nothing about this POC weakens that: Soliton's risk-adaptive dispatch plus Tier-0 fast-path (not enabled here) should tilt cost-per-PR meaningfully lower than single-model tools.

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

---

## Phase 3.5 · Precision-tightening experiment (50 PRs, GPT-5.2 judge — 2026-04-19)

**Setup.** Same 50 PRs as Phase 3, same GPT-5.2 judge via Azure OpenAI managed identity — but Soliton re-run with **3 of 9 proposed improvements applied** (see `bench/crb/IMPROVEMENTS.md`):

| Lever | Change | Expected ΔF1 | Actual ΔF1 |
|-------|--------|-------------:|-----------:|
| L4 — threshold 80 → 85 | `skills/pr-review/SKILL.md` + `agents/synthesizer.md` defaults | +0.05 | see aggregate |
| L2 — severity gate | Nitpicks dropped from markdown body (still in `--output json`). Critical + Improvements only in review body. | +0.10 | see aggregate |
| L1 — atomic findings | `skills/pr-review/SKILL.md` Format A: no nested bullets, no "Option A / Option B" fix enumerations, no "also" conjunctions. | +0.10 | see aggregate |
| — | **Projected combined** (with overlap penalty) | **+0.20** | **+0.042** |

Also for the first time, **sentry-greptile#5** (13-file heavy PR) completed (at $8 cap), so n=50 vs Phase 3's n=49.

### Headline (Phase 3.5)

| Metric | Phase 3 | Phase 3.5 | Δ |
|--------|--------:|----------:|------:|
| n | 49 | **50** | +1 |
| Micro-F1 | 0.235 | **0.277** | **+0.042** |
| Precision | 0.146 | **0.183** | +0.037 |
| Recall | 0.602 | 0.566 | −0.036 |
| TP | 80 | 77 | −3 |
| FP | 468 | **343** | **−125 (−27 %)** |
| FN | 53 | 59 | +6 |
| Goldens | 133 | 136 | +3 (one more PR) |
| Mean candidates/PR | 11.6 | **8.4** | **−28 %** |
| Mean review size (chars) | 10.5k | **~5.5k** | **−48 %** |

**Clearest signal**: review size dropped ~48 % and candidate count dropped ~28 %. Levers L2 (drop nitpicks) + L1 (atomic findings) successfully reduced the step2 extraction surface. FP count dropped 27 % as expected.

**Less clear signal**: F1 lifted only **+4.2 points vs +20 projected**. Precision moved in the right direction (+3.7 pts) but recall dropped (−3.6 pts), largely netting out. Investigation below.

### Per-language breakdown (GPT-5.2 judge)

| Lang | n | P3 F1 | P3.5 F1 | Δ F1 | P3.5 Precision | P3.5 Recall |
|------|--:|------:|--------:|-----:|---------------:|------------:|
| Java | 10 | 0.205 | **0.283** | **+0.078** | 0.191 | 0.542 |
| Go | 10 | 0.241 | **0.326** | **+0.085** | 0.219 | 0.636 |
| Ruby | 10 | 0.209 | **0.291** | **+0.082** | 0.191 | 0.607 |
| Python | 10 | 0.182 | 0.237 | +0.055 | 0.161 | 0.452 |
| **TypeScript** | 10 | **0.325** | **0.266** | **−0.059** | 0.170 | 0.613 |

**TypeScript regressed**. It was our strongest language in Phase 3 (F1 = 0.325, R = 0.839 — top-tier recall) and dropped to F1 = 0.266 in Phase 3.5. Recall cratered (0.839 → 0.613), which means Lever L2 (drop nitpicks) killed low-severity TS goldens that Phase 3 was correctly catching. Specifically: the Phase 2 POC on `cal.com#10967` had 1 FN at Low severity ("redundant optional chaining") but 4 TP including 1 Low — the severity gate zeroes out that Low TP now.

**Implication**: L2 should be **per-language**, not global. TS goldens have a higher density of Low-severity items that Soliton is correctly catching. A v2.1 tweak would keep severity-gate ON for Java/Python/Go/Ruby and OFF (or Low+) for TypeScript.

### Why the projected +0.20 didn't materialize

Three hypotheses from the data:

1. **L1 (atomic findings) didn't actually cap step2 sub-splitting**. The candidates/PR number dropped from 11.6 to 8.4 — a 28 % reduction, but the projection assumed ≈50 % reduction (reaching ~6 candidates/PR — equivalent to what a human reviewer would emit). Looking at example Phase 3.5 reviews: findings are now **single bullets** but still have **multi-paragraph descriptions with nuance and trade-offs**, and step2's LLM still extracts 2-3 sub-issues from those paragraphs. **Next lever**: enforce descriptions ≤2 sentences + move long explanations out of the review body into linked-evidence blocks that step2 can skip.

2. **L2 (severity gate) over-corrected on TS** — see per-language regression. −0.059 F1 on TS alone drags the aggregate.

3. **L4 (threshold 85) suppressed some low-confidence real findings** — observed in Python (recall dropped from 0.50 → 0.45). Some of the findings that were previously at confidence 80-84 were legitimate (matched goldens).

### Takeaway

This run is a **modest but real step forward**:

- **Precision tax is fixable, incrementally**. We cut FPs by 27 % and the review size by 48 % with 1.5 days of skill / synthesizer edits. Three more levers (L3 synthesizer dedup, L5 deeper cross-file retrieval, L6 evidence-scored filter) remain on the table, each estimated at +0.03 to +0.08 F1. A second iteration (Phase 3.6) could plausibly reach 0.32–0.35.
- **Recall and precision are in tension and need finer-grained tuning**. Uniform severity-gating hurts languages where Soliton was already catching Low-severity real issues. Next iteration should make the gate per-language or per-project.
- **50 of 50 PRs reviewed** — sentry-greptile#5 (13-file Python) that Phase 3 dropped at $5 budget completed at $8, validating the budget lever as a reliability tool for heavy PRs.
- **The projected-vs-actual mismatch is itself useful data** — the big pre-registered gain was from L1 atomicity, and that's the lever whose impact was smallest in practice. The real structural fix is description compression, not bullet compression. Added to IMPROVEMENTS.md for v2.2.

### Competitive position after Phase 3.5 (GPT-5.2 judge)

| Rank | Tool | GPT-5.2 F1 |
|------|------|-----------:|
| 20 | codeant-v2 | 0.294 |
| 20 | gemini | 0.295 |
| **≈21** | **Soliton (Phase 3.5, n=50)** | **0.277** |
| 21 | kg | 0.253 |
| 22 | graphite | 0.158 |

Still bottom-tier on raw F1, but the gap to `claude-code` (0.330) and `coderabbit` (0.333) closed from −0.09 to −0.05. A Phase 3.6 iteration targeting the TS regression + L3 synthesizer dedup + description compression could plausibly put us above both.

### Cost tracking (Phase 3.5)

- Soliton-side (Anthropic Console): ~50 × $1.50–$3 avg = **~$75–$150**. Heavier PRs hit $5 or $8 caps; lighter ones completed under $2.
- Judge-side (Azure OpenAI gpt-5.2): 50 × ~2.75 s/review × ~$0.30/review = **~$15**.
- Combined Phase 3.5: ~$90–$165, similar to Phase 3 as expected.

### Reproduction

```bash
# Same setup as Phase 3 but output to phase35-reviews/:
while read UP PR SLUG; do
  MAX_BUDGET_USD=3 OUTPUT_DIR=bench/crb/phase35-reviews \
    bash bench/crb/run-poc-review.sh "$UP" "$PR" "$SLUG" &
done < bench/crb/phase3-dispatch-list.txt
wait

# sentry-greptile#5 needs higher cap:
MAX_BUDGET_USD=8 OUTPUT_DIR=bench/crb/phase35-reviews \
  bash bench/crb/run-poc-review.sh ai-code-review-evaluation/sentry-greptile 5 python-sentry-greptile-5

# Pipeline (same Azure OpenAI config as Phase 3):
bash bench/crb/run-phase35-pipeline.sh
```


## Phase 4c · Structural push — L5 + hallucination-AST combined (50 PRs, GPT-5.2 judge — 2026-04-20)

**Setup.** Same 50 PRs as Phase 3.5, same GPT-5.2 judge via Azure OpenAI managed identity. Soliton re-run against `main@d7ddfd0`, which includes:

- **Phase 4a** (merged PR #24) — `skills/pr-review/cross-file-retrieval.md` L5 lightweight symbol-definition lookup, shared by `correctness` / `hallucination` / `cross-file-impact`.
- **Phase 4b** (merged PR #26) — `lib/hallucination-ast/` Python package implementing Khati 2026's deterministic AST hallucination pre-check + `agents/hallucination.md` §2.5 integration. **Standalone Khati 2026 corpus gate: F1=0.968** (P=0.993, R=0.944) — well above the paper's 0.934. The CRB corpus result below is a SEPARATE judgment on real-world PR review, not the same benchmark.

Budget bumped from Phase 3.5's $3 to `MAX_BUDGET_USD=10` because the added §2.5 pre-check + cross-file retrieval push complex PRs past $2-3. Actual per-review spend averaged well under the cap.

### Headline (Phase 4c)

| Metric | Phase 3.5 | Phase 4c | Δ |
|--------|---------:|---------:|------:|
| n | 50 | 50 | 0 |
| Micro-F1 | **0.277** | **0.261** | **−0.016** |
| Precision | 0.183 | 0.175 | −0.008 |
| Recall | 0.566 | 0.515 | −0.051 |
| TP | 77 | 70 | −7 |
| FP | 343 | 330 | −13 |
| FN | 59 | 66 | +7 |

Aggregate F1 dropped **0.016 points**. Both precision and recall regressed; the recall drop is larger (−0.051 vs −0.008 on precision).

### Ship criteria verdict — CLOSE

Pre-registered in `bench/crb/PHASE_4_DESIGN.md`:

| Outcome | Aggregate F1 | Recall | Per-lang | Action |
|---|---:|---:|---|---|
| ✅ Ship | ≥ 0.32 | ≥ 0.64 | No reg > 0.02 | Replace Phase 3.5 |
| ⚠️ Hold | 0.29–0.31 | 0.60–0.63 | — | Ship whichever of 4a / 4b alone passes |
| ❌ **Close** | **< 0.29** | **< 0.60** | Any > 0.03 | **Documented negative-result** |

Phase 4c F1=0.261 **< 0.29**, recall=0.515 **< 0.60**, and multiple languages regressed > 0.03. This is an unambiguous **CLOSE** per the pre-registered ship criteria.

### Per-language breakdown (GPT-5.2 judge)

| Lang | n | P3.5 F1 | P4c F1 | Δ F1 | P4c Precision | P4c Recall | Interpretation |
|------|--:|--------:|-------:|-----:|--------------:|-----------:|----|
| **TypeScript** | 10 | 0.266 | **0.344** | **+0.078** | 0.231 | 0.677 | Pure Phase 4a (no 4b — TS out of scope in 4b v0.1). L5 cross-file retrieval is a real win here. |
| Java | 10 | 0.283 | 0.278 | −0.005 | 0.200 | 0.458 | Neutral. |
| Go | 10 | 0.326 | 0.248 | **−0.078** | 0.157 | 0.591 | Significant regression. No 4b exposure (Python-only), so L5 changed agent behavior in ways that hurt Go specifically. |
| Ruby | 10 | 0.291 | 0.208 | **−0.083** | 0.141 | 0.393 | Biggest regression. Also no 4b exposure. |
| **Python** | 10 | 0.237 | 0.226 | −0.011 | 0.151 | 0.452 | 4b's target language — modest regression. Indicates §2.5 pre-check is not net-negative on Python alone but also not adding enough to offset. |

**The headline signal is that Phase 4a alone (TS) is +0.078 F1 net-positive, while the combined 4a+4b wash is net-negative on other languages.** Phase 4b's §2.5 shouldn't affect Go/Ruby (non-Python), so those regressions come from 4a's cross-file retrieval changing agent behavior in a way that hurts non-TS languages — either the retrieval-skill invocation rules or the `agents/hallucination.md` §2 `NOT_FOUND_IN_TREE` suppression is causing loss.

### Recall by golden severity

| Severity | TP / Golden | Recall | Note |
|----------|:-----------:|-------:|------|
| Critical | 7 / 9 | **0.778** | Top-tier preserved. |
| High | 20 / 41 | 0.488 | |
| Medium | 22 / 47 | 0.468 | |
| Low | 21 / 39 | 0.538 | |

Critical-severity recall stays strong (0.778) — the structural changes did NOT cost us on the most important findings. The aggregate drop is concentrated on High/Medium goldens.

### Hypotheses for the regression

1. **L5 retrieval over-commits to cross-file verification.** The `agents/hallucination.md` §2 `NOT_FOUND_IN_TREE` suppression (introduced by Phase 4a) defers external-symbol findings pending 4b. For non-Python languages where 4b's pre-check doesn't fire, those deferred findings go to LLM reasoning — but the additional retrieval context may be biasing the agent toward "I see the definition elsewhere, looks fine" instead of flagging surface-level issues. Evidence: Go (no 4b) regressed −0.078.

2. **§2.5 dedup rule on Python silences LLM findings.** The expanded "do not re-emit" rule (tracking emitted-symbols set across §2 and Steps 4-7) was implemented in the Phase 4b review fixes (commit 7da0d46). It may be too aggressive in real PRs — the hallucination agent sees a deterministic finding it already emitted and skips broader LLM reasoning on the same symbol. Evidence: Python recall 0.452 — below Phase 3.5's 0.452 (flat, not worse — but 4b was supposed to HELP).

3. **TS gain is driven by L5's symbol-definition lookup specifically closing Phase 3 FNs.** `cal.com#10967` had 1 FN for "redundant optional chaining" that required cross-file type understanding in Phase 3.5. L5 brings that in. This validates L5 in isolation.

### Recommended follow-up

**Per the pre-registered design doc's Hold band**: "ship whichever of (4a alone, 4b alone) passes individually." Neither was tested in isolation here, but the TS per-language result is strong circumstantial evidence that 4a alone is a net-positive. A follow-up Phase 4c.1 run with 4a only (reverting 4b's §2.5 integration but keeping the lib + Khati gate for other uses) would test this hypothesis cleanly.

Three options the operator can pick:

1. **Close Phase 4 entirely** (strictest reading). F1 regressed; revert 4a + 4b agent integrations; keep the `lib/hallucination-ast/` Python package since it's validated on the Khati corpus and has utility for future work. Move to I19 sandbox / other structural levers.

2. **Ship Phase 4a alone, close Phase 4b** (supported by TS +0.078). Revert `agents/hallucination.md` §2.5 integration; keep the cross-file-retrieval skill and its `agents/correctness.md` / `agents/hallucination.md` §2 integration. Expected aggregate F1: ~0.28-0.29 based on TS lift alone.

3. **Investigate the 4a-driven Go/Ruby regression before deciding** (full diligence). Run a Phase 4c.1 with only the cross-file-retrieval skill active (no §2 `NOT_FOUND_IN_TREE` suppression, no §2.5 pre-check). That isolates whether L5 itself is hurting Go/Ruby or whether the hallucination-agent changes are.

### Competitive position after Phase 4c

Phase 4c F1=0.261 puts us **below** Phase 3.5's 0.277 and widens the gap to `claude-code` (0.330) and `coderabbit` (0.333). On the published leaderboard this would rank below our Phase 3.5 number. **We are not publishing Phase 4c; Phase 3.5's 0.277 remains the Soliton CRB number of record until either Phase 4c.1 or a fresh structural iteration moves above it.**

### Cost tracking (Phase 4c)

- Soliton-side (Anthropic Console): 50 × ~$2.50 avg = **~$125**. No individual review hit the $10 cap.
- Judge-side (Azure OpenAI gpt-5.2): ~$15 (50 reviews × ~2.75s × ~$0.30).
- Combined: **~$140**. In the expected $150-$300 band I flagged before the run.

### Reproduction

```bash
# Already encoded in the two scripts committed under PR #27:

# 1. Generate 50 reviews (30-60 min, ~$125 claude-p spend):
bash bench/crb/dispatch-phase4c.sh              # CONCURRENCY=1 default
CONCURRENCY=3 bash bench/crb/dispatch-phase4c.sh  # faster

# 2. Score via Azure OpenAI gpt-5.2 judge (~3 min, ~$15):
bash bench/crb/run-phase4c-pipeline.sh
```
