---
# v1 flat fields
threshold: 85
agents: auto
sensitive_paths:
  - "auth/"
  - "security/"
  - "payment/"
  - "*.env"
  - "*migration*"
  - "*secret*"
  - "*credential*"
  - "*token*"
  - "*.pem"
  - "*.key"
skip_agents: []
default_output: markdown
feedback_mode: false

# v2 feature-flag fields (all default OFF; opt in per-repo)
# tier0:
#   enabled: true                  # Step 2.6 — gitleaks/osv-scanner/semgrep/lang-lint deterministic gate
#   skip_llm_on_clean: true        # Fast-path approve when Tier-0 verdict=clean
# spec_alignment:
#   enabled: true                  # Step 2.7 — Haiku reads REVIEW.md / .claude/specs/ / PR checklist
# graph:
#   enabled: true                  # Step 2.8 — pre-built graph for blast radius / dep breaks / etc.
#   path: .code-review-graph/graph.db
#   timeout_ms: 20000
# synthesis:
#   realist_check: true            # Step 5.5 — Sonnet pressure-tests CRITICAL findings post-synth
#   realist_threshold: 85
# agents:
#   silent_failure:
#     enabled: true                # default OFF since v2.1.1 (Phase 5.3 evidence); opt in for production
#   comment_accuracy:
#     enabled: true                # default OFF since v2.1.1 (Phase 5.3 evidence); opt in for production
#   cross_file_retrieval_java:
#     enabled: true                # default OFF (Phase 6 experimental, awaiting CRB SHIP); opt in only for Phase 6b $140 measurement run. See bench/crb/PHASE_6_DESIGN.md.
# stack:                                # NOTE: stack-mode is currently CLI-only (--stack-auto / --parent <N> / --parent-sha <SHA> per SKILL.md Step 1 Mode B). The fields below are placeholders for a future SKILL.md Step 2 mapping; uncommenting them today does NOT enable behavior. Tracked under POST_V2_FOLLOWUPS §G3 (orchestrator partial-closure remaining arms).
#   auto_detect: true              # Step 1 Mode B step 4 — auto-detect parent via gt log when --stack-auto and gt on PATH
#   require_parent_merged_check: true   # error if parent PR not yet merged vs its own base
---

# Soliton Configuration

This file configures the Soliton PR Review skill.
Place it at `.claude/soliton.local.md` in your project root.

## v1 options (always available)

- **threshold**: Minimum confidence score (0-100) to surface findings. Default: 85 (raised from 80 in Phase 3.5 — tuned from CRB FP analysis, trims ~15% stylistic nits without material recall loss).
- **agents**: Force specific agents (comma-separated) or `auto` for risk-adaptive dispatch. Default: auto.
- **sensitive_paths**: Glob patterns for files that increase risk score.
- **skip_agents**: Agents to skip. Available agents: correctness, security, hallucination, test-quality, consistency, cross-file-impact, historical-context, spec-alignment, silent-failure, comment-accuracy, realist-check. Hardcoded default skips: `["test-quality", "consistency"]` (per Phase 5 attribution data — those two agents collectively contributed 31% of CRB FPs at 2.5% combined precision).
- **default_output**: Output format. Options: markdown, json. Default: markdown.
- **feedback_mode**: Generate agent-consumable feedback. Default: false.

## v2 feature-flag options (all default OFF; uncomment to opt in)

- **tier0.enabled**: Run Step 2.6 deterministic gate (gitleaks, osv-scanner, semgrep, language lint) before LLM dispatch. See `rules/tier0-tools.md` for tool catalog + per-language install paths.
- **tier0.skip_llm_on_clean**: Fast-path approve when Tier-0 verdict=`clean` (zero findings + small diff). Saves LLM cost on trivial PRs.
- **spec_alignment.enabled**: Run Step 2.7 Haiku agent that reads REVIEW.md / `.claude/specs/*.md` / linked-issue acceptance criteria / PR-description checklist and emits a `SPEC_ALIGNMENT_START` block + findings for unmet criteria.
- **graph.enabled**: Run Step 2.8 graph-signals lookup against a pre-built graph (`.code-review-graph/graph.db` for partial-mode `code-review-graph` backend, `.json` for full-mode `graph-cli`). Provides blast radius, dependency breaks, taint paths, co-change, criticality.
- **graph.path / graph.timeout_ms**: Graph backend location + per-query timeout (default 20000 ms for partial-mode, 500 ms for full-mode).
- **synthesis.realist_check**: Run Step 5.5 post-synth Sonnet pressure-test of CRITICAL findings. Downgrades require cited "Mitigated by:" rationale.
- **agents.silent_failure.enabled**: Dispatch the `silent-failure` agent when the diff touches error-handling code (try/catch/Promise/optional-chaining patterns). **Default OFF since v2.1.1** — Phase 5.3 CRB measurement showed default-ON regressed F1 by 0.045. Opt in for production review where the specialist findings have UX value.
- **agents.comment_accuracy.enabled**: Dispatch the `comment-accuracy` agent when the diff modifies comment-marker lines. **Default OFF since v2.1.1** — same Phase 5.3 evidence as silent_failure. Opt in for production review.
- **agents.cross_file_retrieval_java.enabled**: When `correctness` agent runs on a diff containing `*.java` files, invoke `skills/pr-review/cross-file-retrieval.md` to populate `CROSS_FILE_CONTEXT` blocks (method calls / interface contracts / overrides / superclass refs resolved via `git grep`, capped at 8 resolutions/agent). **Default OFF (Phase 6 experimental, awaiting CRB SHIP)** — opt in only for the Phase 6b $140 measurement run. See `bench/crb/PHASE_6_DESIGN.md` for pre-registered SHIP/HOLD/CLOSE criteria. Purely additive (no `NOT_FOUND_IN_TREE` suppression rule, unlike the reverted Phase 4a skill).

## Example — full v2 dogfood config

```yaml
---
threshold: 85
graph:
  enabled: true
  path: .code-review-graph/graph.db
  timeout_ms: 20000
tier0:
  enabled: true
  skip_llm_on_clean: true
spec_alignment:
  enabled: true
synthesis:
  realist_check: true
agents:
  silent_failure:
    enabled: true
  comment_accuracy:
    enabled: true
---
```

## Phase 5.3 evidence (why silent-failure + comment-accuracy default-OFF)

Phase 5.3 ran the combined v2.1.0 wirings (realist-check + silent-failure + comment-accuracy + cross-file-impact graphSignals) on the 50-PR CRB corpus. Result: F1 = 0.268 vs Phase 5.2's published 0.313 — a **−0.045 regression** at 5.2σ_Δ paired (well outside the σ_F1=0.0086 noise band). Per-agent attribution showed silent-failure + comment-accuracy emitted high-volume FP (UNMATCHED jumped from ~51 to 180). v2.1.1 reverts those two defaults to OFF.

Integrators wanting them ON (for production review where Hora & Robbes 2026's specialist-finding UX value applies) should explicitly enable via the `agents.silent_failure.enabled: true` / `agents.comment_accuracy.enabled: true` block above.
