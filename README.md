# Soliton

Intelligent multi-agent PR review for Claude Code. Adaptive risk-based dispatch sends 2-7 specialized review agents based on how risky the PR is.

## Install

```bash
claude install andyzengmath/soliton
```

Or clone into your plugins directory:

```bash
git clone https://github.com/andyzengmath/soliton.git ~/.claude/plugins/soliton
```

## Usage

```bash
# Review local branch changes
/pr-review

# Review a GitHub PR
/pr-review 123
/pr-review https://github.com/org/repo/pull/123

# JSON output for piping to other tools
/pr-review --output json

# Agent-consumable feedback for automated remediation
/pr-review --output json --feedback

# Force specific agents
/pr-review --agents security,hallucination

# Skip agents
/pr-review --skip consistency

# Lower confidence threshold to see more findings
/pr-review --threshold 60
```

## How It Works

```
/pr-review [target] [flags]
    |
    +-- Input Normalization (local branch / PR number / URL)
    +-- Config Resolution (defaults < .claude/soliton.local.md < CLI flags)
    +-- Edge Cases (empty diff, binary/generated file filtering, trivial fast-path)
    +-- Large PR Chunking (>1000 lines -> parallel chunks)
    +-- Risk Scoring (6 weighted factors -> 0-100 score)
    +-- Adaptive Agent Dispatch (2-7 agents in parallel)
    +-- Synthesis (deduplicate, filter by confidence, categorize)
    +-- Output (markdown / JSON / agent feedback)
```

### Risk-Adaptive Dispatch

The risk scorer computes a 0-100 score from 6 weighted factors:

| Factor | Weight |
|--------|--------|
| Blast radius (importers of changed files) | 25% |
| Change complexity (control flow vs cosmetic) | 20% |
| Sensitive paths (auth, payments, secrets) | 20% |
| File size and scope | 15% |
| AI-authored signals | 10% |
| Test coverage gap | 10% |

Based on the risk level, agents are dispatched:

| Risk Level | Score | Agents |
|------------|-------|--------|
| LOW | 0-30 | correctness, consistency |
| MEDIUM | 31-60 | + security, test-quality |
| HIGH | 61-80 | + hallucination, cross-file-impact |
| CRITICAL | 81-100 | + historical-context |

### Review Agents

| Agent | Model | Focus |
|-------|-------|-------|
| **correctness** | Sonnet | Logic errors, off-by-one, null handling, race conditions |
| **security** | Opus | OWASP Top 10, data flow analysis, injection, secrets |
| **hallucination** | Opus | Non-existent APIs, wrong signatures, deprecated deps |
| **test-quality** | Sonnet | Coverage gaps, mock-only tests, missing edge cases |
| **consistency** | Sonnet | Naming conventions, import ordering, project patterns |
| **cross-file-impact** | Sonnet | Broken callers, interface mismatches, removed exports |
| **historical-context** | Sonnet | Bug-fix frequency, reverted changes, code churn |

### Output Formats

**Markdown** (default) -- severity-organized findings with code suggestions:
```
## Summary
12 files changed, 347 lines added. 4 findings (1 critical, 2 improvements, 1 nitpick).

## Critical
[security] SQL injection in user_query.py:42 (confidence: 94)
...
```

**JSON** (`--output json`) -- machine-consumable `SynthesizedReview` object.

**Agent Feedback** (`--output json --feedback`) -- `AgentInstruction[]` for coding agent remediation loops.

## Configuration

Create `.claude/soliton.local.md` in your project root (see `templates/soliton.local.md`):

```yaml
---
threshold: 80
agents: auto
sensitive_paths:
  - "auth/"
  - "payments/"
  - "*.env"
skip_agents: []
default_output: markdown
feedback_mode: false
---
```

CLI flags override config file values.

## Project Structure

```
plugin.json              Plugin manifest
skills/pr-review/        Main orchestrator skill
agents/                  9 agent definitions (risk-scorer + 7 reviewers + synthesizer)
rules/                   Risk factors, sensitive paths, generated file patterns
templates/               Sample configuration file
tests/fixtures/          5 test fixtures with synthetic diffs
```

## License

MIT
