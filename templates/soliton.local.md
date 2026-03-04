---
threshold: 80
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
---

# Soliton Configuration

This file configures the Soliton PR Review skill.
Place it at `.claude/soliton.local.md` in your project root.

## Options

- **threshold**: Minimum confidence score (0-100) to surface findings. Default: 80
- **agents**: Force specific agents (comma-separated) or `auto` for risk-adaptive dispatch. Default: auto
- **sensitive_paths**: Glob patterns for files that increase risk score
- **skip_agents**: Agents to skip. Options: correctness, security, hallucination, test-quality, consistency, cross-file-impact, historical-context
- **default_output**: Output format. Options: markdown, json. Default: markdown
- **feedback_mode**: Generate agent-consumable feedback. Default: false
