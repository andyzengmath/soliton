# Soliton × Martian Code Review Bench

Integration scaffold for evaluating Soliton against [`withmartian/code-review-benchmark`](https://github.com/withmartian/code-review-benchmark) — the open leaderboard tracking 11+ AI code-review tools (Qodo, CodeRabbit, Copilot, Cursor BugBot, Claude Code, Gemini, Codex, …).

Tracked in `ROADMAP.md` as item **A · Martian CRB publication (I9)** — closes the biggest procurement-readiness gap: every leader in the 2026 landscape has a CRB number; Soliton has none.

## What CRB is

**Offline benchmark** (what we target): 50 curated PRs from 5 major OSS repos (Sentry · Grafana · Cal.com · Discourse · Keycloak) with human-verified **golden comments** labelled by severity (Low/Medium/High/Critical). An LLM judge matches each tool's review against the golden comments and computes **precision** + **recall** + **F1**.

## How CRB evaluates a tool

```
┌────────────────────┐       ┌─────────────────────┐       ┌──────────────┐
│ 1. Fork 50 PRs     │──────▶│ 2. Tool under eval  │──────▶│ 3. Judge LLM │
│    into a GH org   │       │    posts reviews as │       │   matches    │
│    where the tool  │       │    PR comments      │       │   candidates │
│    is installed    │       │                     │       │   against    │
└────────────────────┘       └─────────────────────┘       │   golden     │
                                                           │   comments   │
                                                           └──────────────┘
                                                                   │
                                                                   ▼
                                                      precision / recall / F1
```

The existing Clojure reference harness (`scripts/claude_clone_and_review.clj`) handles manual clone-and-invoke; for bot-based tools the pattern is pure fork + wait + API-scrape.

## Why this works out-of-the-box for Soliton

Soliton's dogfood workflow (`.github/workflows/soliton-review.yml`, live on `main` as of PR #11) is exactly the reviewer-adapter the CRB pipeline expects:

- Installs on any GH repo
- Triggers on every PR open/synchronize
- Posts markdown review as a PR comment (`--body-file`-hardened after PR #6 / #11 self-reviews)

**No Soliton-side adapter code is required** — we just need to fork the 50 benchmark PRs into a GH org that has the dogfood workflow, wait for reviews to post, then run CRB's steps 1-4 locally.

## Execution plan

### Phase 1 (this PR) — plumbing only

- `bench/crb/README.md` — this doc
- `bench/crb/benchmark-prs.json` — extracted PR list + golden-comment counts + labels (frozen snapshot for reproducibility)
- `bench/crb/RESULTS.md` — placeholder for POC + full-corpus results
- `bench/crb/fork-benchmark-prs.sh` — helper bash script to fork the 51 PRs into a target org

Commit to main so any maintainer can pick up Phase 2.

### Phase 2 (next session or follow-up PR) — POC run

1. Create a throwaway GH org (e.g. `andyzengmath-crb-soliton`) or reuse an existing one.
2. Install `.github/workflows/soliton-review.yml` on that org (cron or per-fork trigger).
3. Run `bench/crb/fork-benchmark-prs.sh --org <org> --limit 5` → forks the first 5 PRs.
4. Wait for Soliton dogfood to post reviews on each forked PR (typically 1-5 min per PR; concurrent across all 5).
5. Clone `withmartian/code-review-benchmark`, create an `extra_tools/soliton/` entry (see "Wiring into CRB's pipeline" below).
6. Run `uv run python -m code_review_benchmark.step1_download_prs --tool soliton`.
7. Run steps 2, 2.5, 3, 4 to compute F1 + precision + recall for the 5-PR sample.
8. Commit POC numbers to `bench/crb/RESULTS.md`.

### Phase 3 — full-corpus run

1. Fork all 51 PRs (loose parallelisation; 1-2 hours wall-clock total given Soliton's 15-60 s latency).
2. Full pipeline.
3. Publish `bench/crb/RESULTS.md` with raw F1 + **cost-normalised F1** (F1 per $ of Anthropic API spend) — the differentiator we call out in `idea-stage/IDEA_REPORT.md` §10. Soliton's risk-adaptive dispatch should win on cost / $-efficiency even if raw F1 lands mid-pack.

### Phase 4 — upstream submission

`withmartian/code-review-benchmark` README.md §"Adding a new tool": "Adding a new tool takes an afternoon — fork the benchmark PRs, trigger the tool, run the pipeline." Open a PR against the benchmark repo adding `soliton` to the evaluated-tools table with our numbers.

## Wiring into CRB's pipeline

CRB's `step1_download_prs.py` expects a mapping from tool name to GitHub reviewer-bot user. Soliton's dogfood workflow posts as the invoking user (`andyzengmath`) via `gh pr comment`, not a dedicated bot account. Options:

1. **Use a dedicated bot account** — create a `soliton-reviewer` GitHub account, install it on the forks org, post reviews via that account's `GITHUB_TOKEN`. Adds CRB-clean author attribution.
2. **Tag-based detection** — add a marker line to every Soliton review comment (e.g. `<!-- soliton-review v2 -->`) and extend CRB's step 1 to detect by marker rather than author. Minimal changes to our side; requires CRB patch.
3. **Patch CRB's step 1 author list** — simplest: add `andyzengmath` (or whatever account posts) to CRB's per-repo reviewer allowlist for the Soliton run only, revert after.

Recommended: **(1) dedicated bot account** — clean separation, reproducible for third parties. But (3) is faster for the POC.

## Known considerations

- **Paths-ignore in the dogfood workflow**: `paths-ignore: ["**.md", "docs/**", "LICENSE"]`. Several benchmark PRs are multi-file including `.md` — those will still trigger Soliton because at least one non-md file is in the diff. PRs that are doc-only won't trigger at all; inspect `benchmark-prs.json` for this edge case.
- **Rate limits**: Anthropic API usage for Soliton × 51 PRs × ~$0.15-$1/PR ≈ $10-$50 for a full run. Tier-0 fast-path skipping will reduce cost on trivial diffs once we enable `tier0.enabled: true` on the forks org's `.claude/soliton.local.md`.
- **Training-data leakage caveat**: the 50 OSS PRs are well-known and Anthropic/OpenAI models may have seen them during training. CRB's `online/` benchmark is designed to mitigate this; we'll note the caveat when publishing.
- **Judge variance**: CRB supports 3 judge models (`claude-opus-4-5-20251101`, `claude-sonnet-4-5-20250929`, `openai_gpt-5.2`). Run Soliton through at least 2 for robustness.

## Relevant upstream files

For the maintainer picking up Phase 2, the files you'll touch in the `withmartian/code-review-benchmark` clone:

- `offline/code_review_benchmark/step1_download_prs.py` — add or extend the `soliton` tool entry (author mapping, per-repo config)
- `offline/code_review_benchmark/step2_extract_comments.py` — no changes needed; it calls Step 1's candidates and runs the LLM extraction per tool
- `offline/golden_comments/*.json` — read-only; the ground truth

## Links

- Upstream: https://github.com/withmartian/code-review-benchmark (124⭐ at time of scaffold)
- Live leaderboard: https://codereview.withmartian.com
- Soliton IDEA_REPORT §5 I9: `idea-stage/IDEA_REPORT.md` — the original ranking of this item
- Soliton COMPETITOR_AGENTS_REVIEW.md: `idea-stage/COMPETITOR_AGENTS_REVIEW.md` — table of the 11 tools currently on the leaderboard with their published F1 (Qodo 60-64 %, CodeRabbit 51-52 %, Copilot ~44 %)
