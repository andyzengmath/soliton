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

### Phase 1 (PR #12, merged) — plumbing only

- `bench/crb/README.md` — this doc
- `bench/crb/benchmark-prs.json` — extracted PR list + golden-comment counts + labels (frozen snapshot for reproducibility)
- `bench/crb/RESULTS.md` — placeholder for POC + full-corpus results
- `bench/crb/fork-benchmark-prs.sh` — helper bash script to fork PRs into a target org (**superseded by `run-poc-review.sh` for Phase 2; retained for Phase 3 fork-based path**)

### Phase 2 (this PR — in flight) — local POC run (5 PRs)

Pivoted from a GH-Actions fork-based run to a **local** run after hitting two hard blockers on the operator's machine: (a) `ANTHROPIC_API_KEY` unavailable due to org policy, (b) `claude setup-token` requires a Claude Pro/Max plan (operator is on Anthropic Console). Neither blocks a local `claude -p` invocation that uses the operator's existing Console login.

The local path trades away dogfood fidelity (no Actions, no fork PRs) for unblocking. Dogfood fidelity returns in Phase 3.

1. Select 5 language-diverse PRs from `benchmark-prs.json` (one per language, mixed severity). Selection lives in `RESULTS.md §"Selected PRs"`.
2. For each selection, run `bench/crb/run-poc-review.sh <upstream-owner/repo> <pr-number> <output-slug>`. This:
   - Creates a sibling `../soliton-poc-work/<slug>-shim/` with `git init + remote add origin https://github.com/<upstream>.git` — just enough for `gh pr view <n>` (no-`--repo` form used by `/pr-review`) to resolve to the upstream repo.
   - Invokes `claude -p --plugin-dir <repo-root> --permission-mode acceptEdits --allowedTools ... Run /pr-review <n>` against the shim.
   - Writes the markdown review to `bench/crb/poc-reviews/<slug>.md`.
   - Explicitly does NOT allow `Bash(gh pr comment *)` — we must not spam upstream PRs.
3. Judge the 5 reviews against `../code-review-benchmark/offline/golden_comments/*.json` using the same semantic-match methodology as CRB's `step3_judge_comments.py` (prompt pair of candidate × golden, ask "same underlying issue?"). Judge model for Phase 2 is **Claude Opus 4.7 in-session** — not CRB's standard judges (Opus-4.5 / Sonnet-4.5 / GPT-5.2). Our F1 is therefore NOT directly comparable to the leaderboard; Phase 3 closes that gap.
4. Write precision / recall / F1 per PR + aggregate into `bench/crb/RESULTS.md §"Phase 2"`.

### Phase 3 — full-corpus run (51 PRs, leaderboard-comparable)

1. Unblock the GH-Actions path:
   - GH org with Soliton workflow installed (`andyzengmath-crb-soliton` or reuse `WRDS-Graph`).
   - `CLAUDE_CODE_OAUTH_TOKEN` org secret OR `ANTHROPIC_API_KEY` org secret (once org policy allows).
2. Patch CRB locally (feature branch, **do not push upstream until leaderboard submission**):
   - `offline/code_review_benchmark/step1_download_prs.py` — add `"soliton"` to `_NON_BOT_TOOLS` so human-authored review comments are collected.
   - `offline/code_review_benchmark/step0_fork_prs.py` — skip `disable_actions` and inject `.github/workflows/soliton-review-bench.yml` into the fork's base branch before push.
3. Fork all 51 PRs via patched `step0_fork_prs.py --org <org> --name soliton --file offline/golden_comments/*.json` (loose parallelisation; 1–2 h wall-clock at Soliton's 15–60 s latency).
4. Run the full CRB pipeline (`step1`, `step2`, `step2_5`, `step3`, `step4`) under at least two judge models for robustness.
5. Publish `bench/crb/RESULTS.md` Phase 3 section with raw F1 + **cost-normalised F1** (F1 per $ of API spend) — the differentiator we call out in `idea-stage/IDEA_REPORT.md` §10. Soliton's risk-adaptive dispatch should win on cost / $-efficiency even if raw F1 lands mid-pack.

### Phase 4 — upstream submission

`withmartian/code-review-benchmark` README.md §"Adding a new tool": "Adding a new tool takes an afternoon — fork the benchmark PRs, trigger the tool, run the pipeline." Open a PR against the benchmark repo adding `soliton` to the evaluated-tools table with our numbers.

## Wiring into CRB's pipeline (Phase 3 only — Phase 2 bypasses this)

Reading `offline/code_review_benchmark/step1_download_prs.py` closely revealed the actual hook isn't an "author allowlist" — tools are detected by the fork's **repo name slug** (pattern `{config}__{repo}__{tool}__PR{N}__{date}`), and review comments are fetched per-tool. The twist: line 111 hardcodes a set `_NON_BOT_TOOLS = frozenset({"claude"})` — for any tool *not* in that set, only `type=Bot` users count as review authors. Soliton posts as a human account (`andyzengmath`), so Phase 3 needs one trivial patch: add `"soliton"` to that set.

Separately, `step0_fork_prs.py` line 197 hardcodes `self.disable_actions(new_repo_name)` — kills the injected Soliton workflow before it can run. Phase 3 needs to either skip that call for the Soliton tool name or fork the script. Additionally the step injects no workflow file; Phase 3 must commit `.github/workflows/soliton-review-bench.yml` onto the fork's base branch before the push.

**Phase 2 skips the pipeline entirely** — reviews are local files, judge is in-session. That sidesteps both patches for the POC.

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
