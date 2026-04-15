# Soliton

**Your AI coding agent writes code 10x faster than you can review it. Soliton reviews it for you.**

A Claude Code plugin that runs 2-7 specialized review agents in parallel, adapting to how risky the PR actually is. Low-risk README fix? Two quick agents, done in seconds. Critical auth refactor? Full 7-agent battery with security data-flow analysis and hallucination detection.

> **Assist Mode** — every review surfaces high-confidence findings organized by severity plus a risk score, but makes no automated merge/reject decisions. You stay in control.

## Why Soliton?

| Problem | How Soliton Helps |
|---------|-------------------|
| AI agents generate code faster than you can review it | Parallel multi-agent review catches issues in seconds, not hours |
| Agent-generated code hallucinates APIs and wrong signatures | Dedicated hallucination detector verifies every new import and function call |
| Generic review tools waste time on low-risk changes | Risk-adaptive dispatch: simple PRs get 2 agents, complex ones get 7 |
| Review findings are vague ("consider improving...") | Every finding includes confidence score, exact location, and concrete fix code |
| No way to feed review results back to coding agents | `--feedback` mode outputs machine-consumable instructions your agent can execute |

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
    │   └── PR mode: gh pr view --json, gh pr diff
    │
    ├── Step 2: Config Resolution
    │   └── Hardcoded defaults < .claude/soliton.local.md < CLI flags
    │
    ├── Step 2.5: Edge Cases
    │   ├── Empty/trivial diffs → fast path
    │   ├── Binary/generated files → auto-filtered
    │   └── Deleted-only PRs → reduced agent set
    │
    ├── Step 2.75: Large PR Chunking
    │   └── >1000 lines → split into <500-line chunks, reviewed in parallel
    │
    ├── Step 3: Risk Scoring (single fast agent)
    │   └── 6 weighted factors → 0-100 score → agent dispatch list
    │
    ├── Step 4: Adaptive Agent Dispatch (parallel)
    │   └── 2-7 agents based on risk level, all run simultaneously
    │
    ├── Step 5: Synthesis
    │   └── Deduplicate, filter by confidence, detect conflicts, categorize
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

### The 7 Review Agents

| Agent | Model | What It Catches |
|-------|-------|-----------------|
| **correctness** | Sonnet | Off-by-one, null dereference, race conditions, infinite loops, missing returns |
| **security** | Opus | OWASP Top 10, SQL/XSS/SSRF injection, hardcoded secrets, auth bypass |
| **hallucination** | Opus | Non-existent APIs (`fs.readFileAsync`), wrong signatures, deprecated methods |
| **test-quality** | Sonnet | Missing coverage, mock-only tests, assertion-free tests, missing edge cases |
| **consistency** | Sonnet | Naming violations, import ordering, style deviations from project patterns |
| **cross-file-impact** | Sonnet | Changed signatures breaking callers, removed exports, type mismatches |
| **historical-context** | Sonnet | Files with high bug-fix frequency, recently reverted changes, code churn |

Security and hallucination agents use **Opus** for deepest reasoning. All others use **Sonnet** for speed.

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
      - name: Checkout repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Clone Soliton plugin
        run: git clone --depth 1 --branch v0.0.2 https://github.com/andyzengmath/soliton.git /tmp/soliton

      - name: Run Soliton review
        uses: anthropics/claude-code-action@v1
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
  plugin.json                  Claude Code plugin manifest
  marketplace.json             Marketplace listing metadata
.cursor-plugin/plugin.json     Cursor Marketplace manifest
skills/pr-review/SKILL.md      Main orchestrator (input → risk → dispatch → synthesize → output)
agents/
  risk-scorer.md               Risk scoring engine (6 weighted factors)
  correctness.md               Logic & correctness reviewer
  security.md                  OWASP Top 10 security reviewer (Opus)
  hallucination.md             AI hallucination detector (Opus)
  test-quality.md              Test coverage & quality reviewer
  consistency.md               Code style & convention reviewer
  cross-file-impact.md         Cross-file breakage detector
  historical-context.md        Git history risk analyzer
  synthesizer.md               Finding merger & deduplicator
rules/
  risk-factors.md              Factor definitions and weights
  sensitive-paths.md           Default sensitive file patterns
  generated-file-patterns.md   Auto-generated/binary file patterns
templates/
  soliton.local.md             Sample config file
examples/workflows/
  soliton-review.yml           GitHub Actions — plugin directory (recommended)
  soliton-review-direct.yml    GitHub Actions — direct prompt (fallback)
  soliton-review-gated.yml     GitHub Actions — CI gate (block on critical)
  soliton-review-interactive.yml GitHub Actions — auto + @claude mentions
docs/
  ci-cd-integration.md         Full CI/CD integration guide
tests/fixtures/                5 test fixtures with synthetic diffs
```

## Contributing

PRs welcome. The plugin is entirely markdown/JSON — no build step, no dependencies.

To test changes, copy the repo into `~/.claude/plugins/soliton/` and run `/pr-review` against any branch with changes.

## License

MIT
