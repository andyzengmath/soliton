# Soliton-pr-review

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code Plugin](https://img.shields.io/badge/Claude_Code-Plugin-blueviolet)](https://github.com/andyzengmath/soliton)
[![Cursor Marketplace](https://img.shields.io/badge/Cursor-Marketplace-orange)](https://cursor.com/marketplace)
[![Agents](https://img.shields.io/badge/Review_Agents-9-brightgreen)](agents/)

**Your AI coding agent writes code 10x faster than you can review it. Soliton reviews it for you.**

A Claude Code & Cursor plugin that runs 2-9 specialized review agents **in parallel**, adapting to how risky the PR actually is. Low-risk README fix? Two quick agents, done in seconds. Critical auth refactor with error-handling and docstring changes? Full 9-agent battery with security data-flow analysis, hallucination detection, silent-failure scanning, and comment-rot checks.

> **Assist Mode** — every review surfaces high-confidence findings organized by severity plus a risk score, but makes no automated merge/reject decisions. You stay in control.

## Why Soliton?

| Problem | How Soliton Helps |
|---------|-------------------|
| AI agents generate code faster than you can review it | Parallel multi-agent review catches issues in seconds, not hours |
| Agent-generated code hallucinates APIs and wrong signatures | Dedicated hallucination detector verifies every new import and function call |
| Generic review tools waste time on low-risk changes | Risk-adaptive dispatch: simple PRs get 2 agents, complex ones get 7 |
| Review findings are vague ("consider improving...") | Every finding includes confidence score, exact location, and concrete fix code |
| No way to feed review results back to coding agents | `--feedback` mode outputs machine-consumable instructions your agent can execute |

> **Validated on real enterprise PRs (two arms).** The [PetClinic scout](bench/graph/enterprise-java-dogfood.md) (Spring Boot 3.5/4.0, 10 PRs, ~$2.38) caught **4 oracle-grade defects**: gradle-wrapper `distributionSha256Sum` removal (CWE-494, missed by the human reviewer); `--release 17` flag drop (independently caught + reverted by maintainer [@snicoll](https://github.com/snicoll) in `fc1c749`); Thymeleaf `${addVisit}` variable-vs-message-key typo; `Collectors.toList()` immutability regression on a JAXB-marshalled API. The [Apache Camel full-swarm arm](bench/graph/enterprise-camel-dogfood.md) (v2.1.2, 10 PRs, ~$3.28) caught **5 CRITICAL + 19 IMPROVEMENT findings** with measured per-agent attribution: NPE in `DefaultModelToStructureDumper` when routeId not found (conf 95, JMX-reachable); JSON route dump leaks credentials by bypassing the XML/YAML `setMask` gate (CWE-200/532, OWASP A09); `trustManagerMapper` asymmetric null guard → SSL handshake NPE; `Files.exists` follows symlinks → `FileAlreadyExistsException` from dangling symlink; NPE in `getJMSMessageTypeForBody` no-arg constructor path. Real swarm dispatch ~6× more findings than single-agent simulation. See writeups for per-PR tables + methodology caveats.

> **Cost-normalised F1.** On the [Martian CRB Phase 5.2 corpus](bench/crb/cost-normalised-f1.md) (50 PRs across Python/TypeScript/Java/Go/Ruby — curated for non-trivial review-quality cases), Soliton's projected mean cost is **$0.366/PR (\$1.17 per F1 unit; F1/$ = 0.855 — HOLD vs §C2 ship threshold of 1.0)** at v2.1.2 risk-adaptive dispatch. In real-world PR streams (with §A1 PetClinic's 60% Tier-0 fast-path eligibility carrying through), the projection drops to **$0.146/PR ($0.47 per F1 unit; F1/$ ≈ 2.14 — SHIP)** — comfortably above the §C2 threshold. CRB measures review-quality-on-hard-cases-per-dollar; real-world measures integrator-cost-per-PR-stream. Both are publishable; see writeup for methodology caveats (per-tier projections, not measurements; harness instrumentation pending). 13 agents total = **9 review** (correctness, security, hallucination, test-quality, consistency, cross-file-impact, historical-context, silent-failure, comment-accuracy) + **4 infrastructure** (risk-scorer, spec-alignment, realist-check, synthesizer); the badge above counts the 9 review agents. **Default install dispatches up to 5 of the 9 review agents at CRITICAL risk** (LOW=1 / MEDIUM=2 / HIGH=4 / CRITICAL=5 — see §The Review Agents below; the hardcoded `skipAgents: ['test-quality', 'consistency']` default removes those two from every tier per Phase 5 CRB attribution evidence — they collectively contributed 31% of CRB FPs at 2.5% combined precision). Max counts climb to 7 (with `skip_agents: []` restoring the two skipped) or 9 (also with `silent-failure` + `comment-accuracy` opted in). Opt-in path: `agents.silent_failure.enabled: true` / `agents.comment_accuracy.enabled: true` in `.claude/soliton.local.md`; both default-OFF since v2.1.1 per Phase 5.3 CRB evidence. `realist-check` (infrastructure) is similarly opt-in. **No competitor on the Martian CRB leaderboard publishes F1/\$**, making the cost-normalised metric above a first-mover claim in the space.

## Quick Start

```bash
# Step 1: Add the marketplace (once)
/plugin marketplace add andyzengmath/soliton

# Step 2: Install the plugin
/plugin install soliton@soliton

# Review your current branch
/pr-review

# Review a GitHub PR
/pr-review 123
```

That's it. Soliton auto-detects your base branch, computes a risk score, dispatches the right agents, and shows you the results.

## Install

**Claude Code (recommended):**
```bash
# Add the marketplace
/plugin marketplace add andyzengmath/soliton

# Install the plugin
/plugin install soliton@soliton
```

**Local development:**
```bash
# Clone and load directly (not cached — useful for development)
git clone https://github.com/andyzengmath/soliton.git
claude --plugin-dir ./soliton
```

**Cursor:** Available on the [Cursor Marketplace](https://cursor.com/marketplace) — search "soliton" or install from repo.

## Update

```bash
# Refresh marketplace listings, then update the plugin
/plugin marketplace update soliton
/plugin update soliton@soliton
```

If the update doesn't pick up the latest version (GitHub CDN caching), clear the local cache and reinstall:
```bash
rm -rf ~/.claude/plugins/cache/soliton
/plugin install soliton@soliton
```

## Usage

```bash
# Local branch review (most common — pre-push review)
/pr-review

# GitHub PR by number or URL
/pr-review 123
/pr-review https://github.com/org/repo/pull/123

# Machine-consumable JSON output
/pr-review --output json

# Feed findings back to your coding agent
/pr-review --output json --feedback

# Force specific agents only
/pr-review --agents security,hallucination

# Skip agents you don't need
/pr-review --skip consistency,historical-context

# See more findings (lower confidence threshold)
/pr-review --threshold 60
```

### Example Output

```
Risk Score: 72/100 (HIGH)
Dispatching 6 review agents...
├── correctness
├── security
├── test-quality
├── consistency
├── hallucination
└── cross-file-impact

## Summary
12 files changed, 347 lines added, 42 deleted.
4 findings (1 critical, 2 improvements, 1 nitpick).
Auth refactor with 1 critical SQL injection.

## Critical
🔴 [security] SQL injection via string concatenation in user_query.py:42 (confidence: 94)
   User input from request.args["username"] flows directly into SQL query
   without parameterization.

   ```suggestion
   cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
   ```
   References: [OWASP A03](https://owasp.org/Top10/A03_2021-Injection/), CWE-89

## Improvements
🟡 [hallucination] `requests.get_async()` does not exist in user_service.py:18 (confidence: 91)
   Did you mean `aiohttp.ClientSession.get()` or `httpx.AsyncClient.get()`?
   Evidence: Searched requests library source — no `get_async` method found.

🟡 [testing] No test coverage for validate_input() in validators.py:30 (confidence: 85)
   New function with 3 branches and nullable input — needs edge case tests.

## Risk Metadata
Risk Score: 72/100 (HIGH) | Blast Radius: 8 dependents | Sensitive Paths: auth/
AI-Authored Likelihood: MEDIUM
(2 additional findings below confidence threshold)
```

## Architecture

```
/pr-review [target] [flags]
    │
    ├── Step 1: Input Normalization
    │   ├── Local mode: git diff, git log, branch detection
    │   ├── PR mode: gh pr view --json, gh pr diff
    │   └── Stack-mode (v2, opt-in via --parent <PR#> / --parent-sha / --stack-auto):
    │       reconstruct delta vs parent PR head per rules/stacked-pr-mode.md
    │
    ├── Step 2: Config Resolution
    │   └── Hardcoded defaults < .claude/soliton.local.md < CLI flags
    │
    ├── Step 2.5: Edge Cases
    │   ├── Empty/trivial diffs → fast path
    │   ├── Binary/generated files → auto-filtered
    │   └── Deleted-only PRs → reduced agent set
    │
    ├── Step 2.6: Tier 0 — Deterministic Gate (v2, opt-in via tier0.enabled)
    │   ├── gitleaks / osv-scanner / semgrep / lang-specific lint
    │   ├── verdict ∈ {clean, advisory_only, needs_llm, blocked}
    │   └── clean + skip_llm_on_clean=true → fast-path approve, skip Steps 2.7-5
    │
    ├── Step 2.7: Spec Alignment (v2, opt-in via spec_alignment.enabled)
    │   └── Haiku agent reads REVIEW.md / .claude/specs/ / PR-description checklist
    │       → emits SPEC_ALIGNMENT_START block + findings for unmet criteria
    │
    ├── Step 2.8: Graph Signals (v2, opt-in via graph.enabled)
    │   └── Reads pre-built graph (full-mode graph-cli, partial-mode code-review-graph)
    │       → blast radius, dependency breaks, taint paths, co-change, criticality
    │
    ├── Step 2.75: Large PR Chunking
    │   └── >1000 lines → split into <500-line chunks, reviewed in parallel
    │
    ├── Step 3: Risk Scoring (single fast agent)
    │   └── 6 weighted factors → 0-100 score → agent dispatch list
    │
    ├── Step 4: Adaptive Agent Dispatch (parallel)
    │   └── 2-9 agents based on risk level + content triggers, all run simultaneously
    │       (silent-failure + comment-accuracy default-OFF since v2.1.1; opt in via local config)
    │
    ├── Step 5: Synthesis
    │   └── Deduplicate, filter by confidence, detect conflicts, categorize
    │
    ├── Step 5.5: Realist Check (v2, opt-in via synthesis.realist_check)
    │   └── Sonnet pressure-tests CRITICAL findings; downgrades require cited Mitigated-by
    │
    └── Step 6: Output
        ├── Markdown (human-readable, default)
        ├── JSON (machine-consumable)
        └── Agent Feedback (coding agent remediation)
```

### Risk-Adaptive Dispatch

The risk scorer computes a 0-100 score from 6 weighted factors:

| Factor | Weight | How It's Computed |
|--------|--------|-------------------|
| Blast radius | 25% | Count files importing changed files |
| Change complexity | 20% | Control-flow changes vs cosmetic |
| Sensitive paths | 20% | Matches auth/, payment/, *.env, etc. |
| File size/scope | 15% | Total lines changed |
| AI-authored signals | 10% | Agent commit signatures, uniform style |
| Test coverage gap | 10% | Production files without test changes |

The score determines how many agents are dispatched:

| Risk | Score | Agents | Typical Latency |
|------|-------|--------|-----------------|
| LOW | 0-30 | 2 (correctness, consistency) | ~15s |
| MEDIUM | 31-60 | 4 (+security, test-quality) | ~30s |
| HIGH | 61-80 | 6 (+hallucination, cross-file-impact) | ~45s |
| CRITICAL | 81-100 | 7 (+historical-context) | ~60s |

> **Note on default config:** the table shows what risk-scorer *recommends* before the skipAgents filter. The shipped default is `skipAgents: ['test-quality', 'consistency']` (per Phase 5 attribution data — those two agents collectively contributed 31% of CRB FPs at 2.5% combined precision). Effective default dispatch counts are LOW=1, MEDIUM=2, HIGH=4, CRITICAL=5. Set `skip_agents: []` in `.claude/soliton.local.md` to restore the table's full counts. v2 also content-triggers `silent-failure` (when diff touches error-handling code) and `comment-accuracy` (when diff modifies comments) — both default-OFF as of v2.1.1, opt in via `agents.silent_failure.enabled: true` / `agents.comment_accuracy.enabled: true`. Each adds +1 to the dispatched count when enabled. Realist Check (Step 5.5) is a post-synthesis pass, not a finding-emitter; opt in via `synthesis.realist_check: true`. Max-dispatch count is **9** when all content-triggers fire.

### The Review Agents

| Agent | Model | What It Catches | Default |
|-------|-------|-----------------|---------|
| **correctness** | Sonnet | Off-by-one, null dereference, race conditions, infinite loops, missing returns | ON |
| **security** | Opus | OWASP Top 10, SQL/XSS/SSRF injection, hardcoded secrets, auth bypass | ON |
| **hallucination** | Opus | Non-existent APIs (`fs.readFileAsync`), wrong signatures, deprecated methods | ON |
| **test-quality** | Sonnet | Missing coverage, mock-only tests, assertion-free tests, missing edge cases | OFF (`skipAgents` default) |
| **consistency** | Sonnet | Naming violations, import ordering, style deviations from project patterns | OFF (`skipAgents` default) |
| **cross-file-impact** | Sonnet | Changed signatures breaking callers, removed exports, type mismatches | ON |
| **historical-context** | Sonnet | Files with high bug-fix frequency, recently reverted changes, code churn | ON |
| **spec-alignment** (v2 Step 2.7) | Haiku | Acceptance-criteria mismatches in PR description / REVIEW.md / `.claude/specs/` | OFF (opt in via `spec_alignment.enabled: true`) |
| **silent-failure** (v2 Step 4.1) | Sonnet | Empty catches, swallowed Promises, optional-chaining nullability hides, mock-in-prod, assertion-free tests | OFF as of v2.1.1 (was ON in v2.1.0; opt in via `agents.silent_failure.enabled: true`) |
| **comment-accuracy** (v2 Step 4.1) | Haiku | Docstring/comment rot, stale `@deprecated`, example-code drift, NOTE/TODO contradictions | OFF as of v2.1.1 (was ON in v2.1.0; opt in via `agents.comment_accuracy.enabled: true`) |
| **realist-check** (v2 Step 5.5) | Sonnet | Post-synthesis pressure-test of CRITICAL findings; downgrades require cited Mitigated-by | OFF (opt in via `synthesis.realist_check: true`) |

Security and hallucination agents use **Opus** for deepest reasoning. All others use **Sonnet** or **Haiku** for speed.

### Output Formats

| Format | Flag | Use Case |
|--------|------|----------|
| **Markdown** | (default) | Human review in terminal |
| **JSON** | `--output json` | Pipe to CI tools, dashboards, or custom scripts |
| **Agent Feedback** | `--output json --feedback` | Feed back to coding agents for automated remediation |

The Agent Feedback format transforms each finding into an `AgentInstruction` with `action` (fix/replace/remove/add-test/investigate), exact file/line references, `currentCode`, `suggestedCode`, and priority — so a coding agent can execute fixes without human translation.

## CI/CD Integration (GitHub Actions)

Run Soliton automatically on every pull request. Add this workflow to your repo:

```yaml
# .github/workflows/soliton-review.yml
name: Soliton PR Review

on:
  pull_request:
    types: [opened, synchronize]

concurrency:
  group: soliton-${{ github.event.pull_request.number }}
  cancel-in-progress: true

jobs:
  review:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
      pull-requests: write
      issues: write

    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Clone Soliton
        run: git clone --depth 1 --branch v2.1.1 https://github.com/andyzengmath/soliton.git /tmp/soliton

      - uses: anthropics/claude-code-action@v1
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          claude_args: --plugin-dir /tmp/soliton
          prompt: |
            Run /pr-review ${{ github.event.pull_request.number }}

            After the review completes, post the full markdown review output as a
            PR comment using:
            gh pr comment ${{ github.event.pull_request.number }} --body "<review>"
          allowed_tools: |
            Read
            Grep
            Glob
            Bash(git diff *)
            Bash(git log *)
            Bash(git show *)
            Bash(git branch *)
            Bash(gh pr comment *)
            Bash(gh pr diff *)
            Bash(gh pr view *)
            Agent
```

**Prerequisites**: Add `ANTHROPIC_API_KEY` as a repository secret (Settings → Secrets → Actions).

**More strategies** (CI gating, interactive `@claude` mentions, Bedrock/Vertex auth, cost optimization):
see the [full CI/CD integration guide](docs/ci-cd-integration.md) and [example workflows](examples/workflows/).


## Configuration

Create `.claude/soliton.local.md` in your project root:

```yaml
---
threshold: 80            # Min confidence to surface findings (0-100)
agents: auto             # 'auto' for risk-adaptive, or comma-separated list
sensitive_paths:         # Glob patterns that increase risk score
  - "auth/"
  - "payment/"
  - "*.env"
  - "*secret*"
skip_agents: []          # Agents to always skip
default_output: markdown # 'markdown' or 'json'
feedback_mode: false     # Generate agent-consumable instructions
---
```

CLI flags always override config file values. A sample template is at `templates/soliton.local.md`.

## Flags Reference

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--threshold N` | 0-100 | 80 | Minimum confidence to show a finding |
| `--agents list` | comma-sep | auto | Force specific agents |
| `--skip list` | comma-sep | none | Skip specific agents |
| `--sensitive-paths glob` | comma-sep | see config | Override sensitive patterns |
| `--output format` | markdown/json | markdown | Output format |
| `--feedback` | flag | false | Agent feedback mode (requires `--output json`) |
| `--branch name` | string | auto | Override head branch |

## Project Structure

```
.claude-plugin/
  plugin.json                  Claude Code plugin manifest (agents + skills + commands arrays)
  marketplace.json             Marketplace listing metadata
.cursor-plugin/plugin.json     Cursor Marketplace manifest
skills/pr-review/
  SKILL.md                     Main orchestrator (input → risk → dispatch → synthesize → output)
  graph-signals.md             Tier-1 cross-file queries — gated on graph-cli ecosystem (sibling repo)
  cross-file-retrieval.md      Phase 6 Java-only L5 retrieval (default-OFF; awaiting CRB SHIP)
agents/                        9 review + 4 infrastructure agents (default install dispatches up to 5
                               at CRITICAL; up to 9 with silent-failure + comment-accuracy opted in)
  risk-scorer.md               Risk scoring engine (6 weighted factors)
  correctness.md               Logic & correctness reviewer (Phase 6 §2.5 conditional)
  security.md                  OWASP Top 10 security reviewer (Opus)
  hallucination.md             AI hallucination detector (Opus)
  test-quality.md / consistency.md  Default-skipped per Phase 5 attribution evidence
  cross-file-impact.md         Cross-file breakage detector
  historical-context.md        Git history risk analyzer
  spec-alignment.md            Step 2.7 spec-vs-PR alignment (Haiku, opt-in)
  silent-failure.md / comment-accuracy.md  Default-OFF since v2.1.1 (Phase 5.3 evidence)
  realist-check.md             Step 5.5 critical-finding pressure-test (Sonnet, opt-in)
  synthesizer.md               Finding merger & deduplicator
commands/                      Slash commands (Phase 6+; A2 §1.4 — 3 of 7 shippable today)
  blast-radius.md              /blast-radius <file> — grep-based importer count + sensitive flag
  co-change.md                 /co-change <file> — git-log heuristic for CO_CHANGE candidates
  review-pack.md               /review-pack <ref> — Step 1+2+2.5+2.75 preview (no agent dispatch)
hooks/                         Optional Claude Code hooks (default-OFF; user-installed via settings.json)
  blast-radius-warning.sh      Hook C — PostToolUse advisory on Edit/Write (grep-backed)
lib/hallucination-ast/         Standalone Python package implementing Khati 2026's deterministic
                               AST hallucination pre-check. F1=0.968 standalone; NOT wired into
                               agents/hallucination.md (Phase 4 revert; tracked under POST_V2_FOLLOWUPS §D5).
rules/
  risk-factors.md              Factor definitions and weights
  sensitive-paths.md           Default sensitive file patterns
  generated-file-patterns.md   Auto-generated/binary file patterns
  model-pricing.md             Per-MTok rate sheet + costUsd algorithm
  model-tiers.md               Step-by-step Haiku/Sonnet/Opus assignments
  stacked-pr-mode.md           --parent / --parent-sha / --stack-auto orchestrator spec
  tier0-tools.md               Tier-0 deterministic gate tool catalog
templates/
  soliton.local.md             Sample config file (tier0 + spec_alignment + graph + agents.* flags)
examples/workflows/
  soliton-review.yml           GitHub Actions — plugin directory (recommended)
  soliton-review-direct.yml    GitHub Actions — direct prompt (fallback)
  soliton-review-gated.yml     GitHub Actions — CI gate (block on critical)
  soliton-review-interactive.yml GitHub Actions — auto + @claude mentions
docs/                          User-facing documentation
  ci-cd-integration.md         Full CI/CD integration guide
  hooks-integration.md         Hook wiring guide (Hook C; Hooks A & B deferred)
  prd-soliton.md               Product requirements doc
  pr-faq-soliton.md            Internal PR-FAQ
  self-validation-evidence.md  Catalog of self-validation events (procurement-grade artifact)
bench/crb/                     CRB benchmark infrastructure + writeups
  RESULTS.md                   Canonical phase log (Phase 5.2 F1=0.313 = number of record)
  IMPROVEMENTS.md              Levers-tried catalog with σ-floor + subtraction-wins doctrine
  PHASE_6_DESIGN.md            Java-only L5 design (pre-registered ship criteria)
  cost-normalised-f1.md        F1/$ derivation (CRB 0.855 / real-world 2.14; first-mover claim)
  martian-submission-template.md  §B3 upstream submission template (auth-gated on PR #65)
  sphinx-actionability-spec.md Judge-prompt addendum spec (sibling-repo work)
  judge-noise-envelope.md      σ_F1 = 0.0086 calibration (PR #48)
  dispatch-phase6.sh / run-phase6-pipeline.sh  Phase 6b dispatch + scoring scripts
tests/                         Test infrastructure
  fixtures/                    16 fixtures (v1: 5 + v2 wirings: 4 + Tier-0/Spec/Phase-6: 7)
  run_fixtures.py              Fixture runner (--mode structural | phase4b | all)
  check_feature_flag_plumbing.py  Regression check for agents.*.enabled flag wiring
.github/workflows/             CI workflows
  fixture-runner.yml           Runs tests/run_fixtures.py on PR + push
  feature-flag-plumbing.yml    Runs tests/check_feature_flag_plumbing.py on PR + push
  hallucination-ast-tests.yml  pytest gate for lib/hallucination-ast/
```

**Independent quality-signal stack** (procurement-relevant, as of v2.1.2 + cross-walk delivery):

1. **F1 = 0.313** — Martian CRB Phase 5.2 (50-PR offline, GPT-5.2 judge) — see `bench/crb/RESULTS.md`
2. **F1/$ = 2.14 real-world** / **0.855 CRB** — first-mover claim per 2026-05-01 SOTA research; no competitor publishes F1/$ — see `bench/crb/cost-normalised-f1.md`
3. **Self-validation evidence catalog** — 8 documented dogfood events where Soliton's review pipeline caught its own bugs at multiple severity tiers — see `docs/self-validation-evidence.md`
4. **Sphinx actionability spec** — pre-registered judge-prompt addendum measuring "would the developer actually change code?" — see `bench/crb/sphinx-actionability-spec.md`

## Contributing

PRs welcome. The plugin is entirely markdown/JSON — no build step, no dependencies.

To test changes, copy the repo into `~/.claude/plugins/soliton/` and run `/pr-review` against any branch with changes.

## License

MIT
