# Soliton CRB Results

Placeholder for benchmark outputs. Populated as Phase 2 (POC) and Phase 3 (full corpus) execute.

## Phase 2 · POC (5 PRs)

*Not yet run.* Sample: first 5 PRs from `benchmark-prs.json`.

| Run ID | Date | Judge model | Precision | Recall | F1 | Avg cost/PR | Avg latency/PR |
|---|---|---|---|---|---|---|---|
| _tbd_ | _tbd_ | _tbd_ | _tbd_ | _tbd_ | _tbd_ | _tbd_ | _tbd_ |

## Phase 3 · Full corpus (51 PRs)

*Not yet run.* Full offline-benchmark corpus per `benchmark-prs.json`.

### Headline

| Metric | Value |
|---|---|
| F1 | _tbd_ |
| F1 (cost-normalised — F1 per $ of API spend) | _tbd_ |
| Precision | _tbd_ |
| Recall | _tbd_ |
| Mean cost/PR | _tbd_ |
| Mean latency/PR | _tbd_ |

### Per-language breakdown

| Language | PR count | Soliton F1 | Qodo (reference) | CodeRabbit (reference) |
|---|---|---|---|---|
| Python (Sentry) | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| Go (Grafana) | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| TypeScript (Cal.com) | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| Ruby (Discourse) | _tbd_ | _tbd_ | _tbd_ | _tbd_ |
| Java (Keycloak) | _tbd_ | _tbd_ | _tbd_ | _tbd_ |

### Multi-judge variance (robustness)

| Judge model | Precision | Recall | F1 |
|---|---|---|---|
| `anthropic_claude-opus-4-5-20251101` | _tbd_ | _tbd_ | _tbd_ |
| `anthropic_claude-sonnet-4-5-20250929` | _tbd_ | _tbd_ | _tbd_ |
| `openai_gpt-5.2` | _tbd_ | _tbd_ | _tbd_ |

### Competitive context (from CRB leaderboard at time of run)

Populate with the published F1 of the current-leaderboard entries so we can compare directly. Sourced from `withmartian/code-review-benchmark/offline/results/`.

| Tool | Published F1 | Cost reference |
|---|---|---|
| Qodo Merge | _tbd_ | _tbd_ |
| CodeRabbit | _tbd_ | _tbd_ |
| Copilot | _tbd_ | _tbd_ |
| Greptile | _tbd_ | _tbd_ |
| … | | |

### Caveats to flag in the write-up

- Training-data leakage (PRs are from well-known OSS repos; models may have seen them).
- Judge-model variance (report the judge used per result row).
- Dogfood workflow's `paths-ignore: ["**.md"]` — a benchmark PR that is doc-only won't trigger Soliton; flag any affected PRs from `benchmark-prs.json`.
- Whether Tier-0 was enabled in the forks org's `.claude/soliton.local.md` — affects cost and possibly recall (fast-path clean PRs won't get a full-swarm review).

## Reproduction steps

1. Clone this repo; `cd bench/crb/`.
2. Set `ORG` to your target GH org with Soliton's dogfood workflow installed.
3. `./fork-benchmark-prs.sh --org $ORG` — forks all 51 benchmark PRs.
4. Open the PR branches on each fork (CRB upstream's `step0_fork_prs.py` pattern).
5. Wait for Soliton to review each (~1-2 hours for 51 forks at ~1-5 min each, parallel).
6. Clone `withmartian/code-review-benchmark`; follow `offline/README.md` from Step 1 onwards with `--tool soliton`.
7. Populate this file with the results.
