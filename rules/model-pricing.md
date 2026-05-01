# Model Pricing — rate sheet for Soliton's `metadata.costUsd` instrumentation

Source-of-truth rate table the `/pr-review` orchestrator multiplies against `metadata.totalTokens` to populate `metadata.costUsd` in Format B JSON output (per `skills/pr-review/SKILL.md` Step 6 Format B).

## Why this file exists

C2 cost-normalised F1 (POST_V2_FOLLOWUPS §C2 / IDEA_REPORT G9) needs the orchestrator to emit a measured `costUsd` per review so integrators and benchmark publications can compute F1 ÷ $/PR. The rates below are the inputs to that computation. Phase 1 of §C2 (this file + the SKILL.md schema additions) ships the contract; Phase 2 (Phase 5.2 re-run with counters on) is pre-authorized at \$15–25 once Phase 1 lands on `main`.

## Anthropic API rates (USD per million tokens; verified 2026-04 — re-check at <https://www.anthropic.com/pricing>)

| Model family | Model ID | Input | Output | Cache write (5m) | Cache read |
|---|---|---:|---:|---:|---:|
| Opus 4.x | `claude-opus-4-7` | $15.00 | $75.00 | $18.75 | $1.50 |
| Sonnet 4.x | `claude-sonnet-4-6` | $3.00 | $15.00 | $3.75 | $0.30 |
| Haiku 4.x | `claude-haiku-4-5-20251001` | $1.00 | $5.00 | $1.25 | $0.10 |

**Cache pricing rule** (per Anthropic prompt-caching docs): `cache_creation_input_tokens` are billed at +25% over base input; `cache_read_input_tokens` are billed at 90% off (= 10% of base input). 1-hour cache TTL is +100% over base input (write) but Soliton uses the 5-minute default per `skills/pr-review/SKILL.md`.

## How the orchestrator computes `costUsd`

Each Agent dispatch (Step 3 risk-scorer + Step 4 review agents + Step 5 synthesizer + Step 5.5 realist-check + Step 2.7 spec-alignment) returns an Anthropic API response with a `usage` block containing `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, and `cache_read_input_tokens`. The orchestrator:

1. Tags each Agent dispatch with the model that ran it (per `rules/model-tiers.md` § Step-by-step assignments + § Review-agent model assignments).
2. Sums tokens by model into `metadata.totalTokens.{input,output,cacheCreation,cacheRead}` (aggregated across all models).
3. Computes per-model subtotals using the rates above:
   - `cost_model = (input - cacheRead - cacheCreation) × input_rate`
   - `+ cacheRead × cacheRead_rate`
   - `+ cacheCreation × cacheCreation_rate`
   - `+ output × output_rate`
4. Sums per-model subtotals into `metadata.costUsd`, rounded to 4 decimals (\$0.0001 precision).

The aggregate `totalTokens` block emitted in JSON is not enough on its own to recompute cost — that requires the per-model split, which Soliton does not expose in the public schema. Integrators wanting to recompute can query the same Anthropic API logs by request ID. (Soliton's `costUsd` is the single canonical reported number.)

## Integrator overrides

Integrators on Bedrock or Vertex AI run against different rate sheets — Anthropic charges Anthropic-API rates; Bedrock and Vertex charge their hosting partners' rates. Override per-repo via `.claude/soliton.local.md`:

```yaml
---
costing:
  rate_overrides:
    "claude-opus-4-7":
      input: 15.00
      output: 75.00
      cache_creation: 18.75
      cache_read: 1.50
    "claude-sonnet-4-6":
      input: 3.00
      output: 15.00
    "claude-haiku-4-5-20251001":
      input: 1.00
      output: 5.00
  currency: USD          # Bedrock invoices in USD; Vertex may invoice in regional currency
  emit_per_model: false  # opt in to a metadata.costUsdByModel{} breakdown for cost attribution
---
```

When `costing.rate_overrides.<model_id>` is present, those rates supersede the table above for that model only. Other models continue to use the canonical table.

## Cost-attribution caveat

Soliton's `costUsd` is the cost of the **review pass itself** — the LLM tokens consumed by the agent swarm. It does NOT include:

- The cost of running the underlying coding agent that authored the PR (out of scope; varies by tool).
- Tier-0 deterministic-tool runs (gitleaks, osv-scanner, semgrep, etc.) — these run for free locally; integrators bear their CI minutes cost separately.
- Graph index build / refresh cost (one-time amortised).

For benchmark-publication purposes (CRB cost-normalised F1), `metadata.costUsd` is the right denominator: it captures the marginal review-pass cost an integrator pays per PR.

## v2.1.2 instrumentation status (Phase 1 of §C2)

- ✅ Schema added to `skills/pr-review/SKILL.md` Step 6 Format B `metadata` block.
- ✅ Per-model rate table + computation algorithm documented (this file).
- ⚠️ Orchestrator currently emits `metadata.costUsd` only when running under a harness that surfaces per-Agent `usage` blocks. Claude Code's Agent tool today does NOT surface usage in agent return values, so the orchestrator falls back to a heuristic: tokens estimated from final review markdown length × per-model token-per-character ratio. Integrators wanting precise costing must wrap dispatch to capture per-call usage upstream of the orchestrator. This caveat is also documented in SKILL.md Step 6 Format B.
- ⏳ Phase 2 (Phase 5.2 re-run with the instrumented orchestrator + comparing measured F1 ÷ $/PR against the IDEA_REPORT \$0.10–\$0.40 target) is pre-authorized at ~\$15–25 once Phase 1 lands.

## Rate-update protocol

Anthropic occasionally updates pricing. The cadence is roughly 1× per 6 months. When that happens:

1. Update the per-model rate table in this file.
2. Note the date + previous rates in a new "## Rate change history" section at the bottom (so existing benchmark numbers can be back-calculated).
3. Bump Soliton's plugin version (patch bump fine; user-facing cost changes are not breaking).
4. Sync the override-pattern example block above if any field shape changed.

(No history entries yet — the table is canonical as of v2.1.2 ship.)
