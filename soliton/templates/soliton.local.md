---
threshold: 80
sensitive_paths:
  - "auth/"
  - "security/"
  - "payment/"
  - "*.env"
  - "*migration*"
  - "*secret*"
skip_agents: []
default_output: markdown
feedback_mode: false
---

# Soliton Configuration

This file configures the Soliton PR Review skill.
Place it at `.claude/soliton.local.md` in your project root.

## Options

- **threshold**: Minimum confidence score (0-100) to surface findings. Default: 80
- **sensitive_paths**: Glob patterns for files that increase risk score. Default: auth/, security/, payment/, *.env, *migration*, *secret*
- **skip_agents**: Agents to skip. Options: correctness, security, hallucination, test-quality, consistency, cross-file-impact, historical-context
- **default_output**: Output format. Options: markdown, json. Default: markdown
- **feedback_mode**: Generate agent-consumable feedback. Default: false
