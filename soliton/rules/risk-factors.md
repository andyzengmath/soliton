# Risk Scoring Factors

| Factor | Weight | Description |
|--------|--------|-------------|
| Blast radius | 25% | Number of files importing/referencing changed files |
| Change complexity | 20% | Cyclomatic complexity delta, new branching logic |
| Sensitive paths | 20% | Files matching auth/, security/, payment/, *.env, *migration*, *secret* |
| File size/scope | 15% | Total lines changed, number of files changed |
| AI-authored signals | 10% | Uniform style, boilerplate ratio, agent commit signatures |
| Test coverage gap | 10% | Production files changed without corresponding test changes |

## Risk Levels

| Level | Score Range | Agents Dispatched |
|-------|-------------|-------------------|
| LOW | 0-30 | correctness, consistency |
| MEDIUM | 31-60 | + security, test-quality |
| HIGH | 61-80 | + hallucination, cross-file-impact |
| CRITICAL | 81-100 | + historical-context |
