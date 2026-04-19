# Model-Tier Assignments

Which Anthropic model runs each step of the Soliton pipeline. These assignments are the default;
any step can be overridden via `.claude/soliton.local.md`.

## Design principle

**Haiku for mechanical / structured / dispatch work. Sonnet for review reasoning. Opus for the
two hardest specialist domains — security and hallucination detection.**

This mirrors the `claude-plugins-official/code-review` pattern (Haiku dispatchers + Sonnet
reviewers) and is validated by SWE-PRBench 2026: narrow-context structured LLM work degrades
less than wide-context reasoning, so the cheapest model that can do structured work should.

## Step-by-step assignments

| Step | Component | Default model | Why |
|---|---|---|---|
| 1 | Input normalization | deterministic (shell) | No LLM needed |
| 2 | Config resolution | deterministic (shell) | YAML + merge |
| 2.5 | Edge-case handling | **Haiku** | Pattern matching; filter lists |
| 2.6 | Tier 0 orchestration | **Haiku** | Dispatch tool invocations; parse exit codes |
| 2.7 | Spec alignment | **Haiku** | Structured criterion extraction + grep verification |
| 2.75 | Chunking (large PR) | **Haiku** | Directory + graph-feature grouping heuristic |
| 2.8 | Graph signals | deterministic (CLI) | Graph query; no LLM |
| 3 | Risk scoring | **Haiku** | Mostly arithmetic + lookup on graph signals; upgraded from Sonnet |
| 4 | Review agents (parallel dispatch) | per-agent (see below) | Reasoning |
| 5 | Synthesis — dedup + categorisation | **Haiku** | Pattern matching + sorting |
| 5b | Synthesis — Realist Check (I6) | **Sonnet** | Reasoning about impact |
| 5c | Synthesis — confidence filter | deterministic | Threshold comparison |
| 6 | Output formatting | deterministic | Markdown/JSON template rendering |

## Review-agent model assignments

| Agent | Default model | Rationale |
|---|---|---|
| `correctness` | **Sonnet** | Needs reasoning about logic / edge cases |
| `security` | **Opus** | Deepest reasoning; data-flow + threat modelling |
| `hallucination` | **Opus** | API existence verification across poly-version ecosystems — hard |
| `hallucination AST pre-check` (I4) | deterministic | Khati-2026 zero-LLM; 80 % of cases |
| `test-quality` | **Sonnet** | Mock / coverage reasoning |
| `consistency` | **Haiku** | Mostly CLAUDE.md / REVIEW.md rule matching |
| `cross-file-impact` | **Sonnet** | Reasons about call-site breakage from graph-signals |
| `historical-context` | **Haiku** | Pattern matching on git log + prior-PR comments |
| `silent-failure` (I7, new) | **Sonnet** | Empty-catch / fallback-to-mock detection needs reasoning |
| `comment-accuracy` (I7, new) | **Haiku** | Comment-vs-code alignment is pattern-heavy |
| `spec-alignment` (I3, new) | **Haiku** | Structured; grep-backed |

**Consistency change**: was Sonnet, dropped to Haiku because the agent's prompt is now almost
entirely "does this diff violate any rule listed in CLAUDE.md / REVIEW.md / .claude/rules/?"
— a pattern-match. Saves meaningful tokens on every review. If a repo's consistency rules
are heavy on reasoning (e.g., "architectural patterns"), override in `soliton.local.md`.

## Cost projection

For a MEDIUM-risk PR (previously 4 Sonnet agents):

| Component | Today | After I5 |
|---|---|---|
| Eligibility + filtering | Sonnet | Haiku |
| Risk scorer | Sonnet | Haiku |
| correctness | Sonnet | Sonnet (unchanged) |
| consistency | Sonnet | Haiku |
| security | Opus | Opus (unchanged) |
| test-quality | Sonnet | Sonnet (unchanged) |
| Synthesis dedup | Sonnet | Haiku |
| Synthesis Realist Check | — | Sonnet (new) |
| **Estimated cost** | ~$0.40 | **~$0.22** (45 % drop) |

Opus cost is unchanged (one Opus call for security + one for hallucination on HIGH+). Sonnet
calls are reduced 4× on MEDIUM PRs.

## Override patterns

In `.claude/soliton.local.md`:

```yaml
modelTiers:
  # Override a single agent
  agents:
    consistency: sonnet       # If your consistency rules need reasoning
    historical-context: sonnet

  # Override an orchestration step
  orchestration:
    risk_scorer: sonnet       # If the risk scorer is underperforming on Haiku

  # Force-upgrade everything on CRITICAL PRs (one-liner)
  criticalTierBoost: true     # CRITICAL PRs get Sonnet minimum on all steps
```

## Compatibility note — Claude 4 family

Default model IDs (as of April 2026):
- `haiku` → `claude-haiku-4-5-20251001`
- `sonnet` → `claude-sonnet-4-6`
- `opus` → `claude-opus-4-7`

Override via `claude_args` in the GitHub Actions workflow, or rely on Claude Code's default
model. Do NOT hardcode model IDs in agent frontmatter — the `model: haiku | sonnet | opus`
alias pattern future-proofs against model releases (oh-my-openagent's model-by-category
pattern, `OSS_ECOSYSTEM_REVIEW.md §8c`).

## Monitoring

`.soliton/state/runs/<pr-or-sha>.json` logs per-step:
- model used
- prompt-token / completion-token counts
- duration_ms

This is the input to the cost dashboard (see `docs/ci-cd-integration.md` §Cost Optimization).
