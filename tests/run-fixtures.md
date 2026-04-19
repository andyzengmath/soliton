# Test Fixture Validation Guide

This document describes how to validate the Soliton PR Review skill against the test fixture corpus.

## Fixtures

### v1 fixtures (original swarm)

| Fixture | Expected Risk | Expected Findings | Expected Category | Expected Severity |
|---------|--------------|-------------------|-------------------|-------------------|
| trivial-readme-fix | 0-10 | 0 | (none) | (none) |
| sql-injection | 60-100 | 1+ | security | critical |
| hallucinated-api | 30-80 | 1+ | hallucination | critical |
| missing-tests | 30-70 | 1+ | testing | improvement+ |
| cross-file-break | 50-90 | 1+ | cross-file-impact | critical |

### v2 fixtures (Tier-0, Spec Alignment, Graph Signals)

Require the corresponding feature flag enabled in `.claude/soliton.local.md`.

| Fixture | Flag | Expected Verdict | LLM Swarm | Notes |
|---------|------|------------------|-----------|-------|
| tier0-clean | `tier0.enabled` + `skip_llm_on_clean` | `clean` | **skipped** | trivial whitespace diff; fast-path must fire |
| tier0-blocked-secret | `tier0.enabled` | `blocked` (`secret_leaked`) | **skipped** | fake-but-gitleaks-matching AWS key; CI must exit 1 |
| tier0-advisory-only | `tier0.enabled` | `advisory_only` | runs with `--threshold 90` | lint-only findings; verdict promotion transitions to advisory_only rather than needs_llm |
| spec-alignment-unmet-checklist | `spec_alignment.enabled` | — | runs | REVIEW.md mandates `logRequest(req)` + test file; handler lacks both; mechanical wiring-verification emits confidence-100 CRITICAL |

Each v2 fixture's `expected.json` adds `tier0Verdict`, `llmSwarmSkipped`, `blockReason` (optional), `confidenceThresholdBumpedTo` (optional), or `wiringChecksFailed` (for spec-alignment) on top of the base fields.

## Validation Process

For each fixture directory in `tests/fixtures/`:

### 1. Read the fixture
```
Read tests/fixtures/<fixture-name>/diff.patch
Read tests/fixtures/<fixture-name>/expected.json
```

### 2. Simulate a review
Run `/pr-review` with the diff as the input. Since we cannot pipe a diff file directly, simulate by:
1. Creating a temporary branch with the changes from the diff
2. Running `/pr-review` against that branch
3. Or manually invoking the risk-scorer and review agents with the diff content

### 3. Verify results

For each fixture, check:

**Risk score:**
- Actual risk score falls within `expected.riskRange` [min, max]
- Risk level matches the expected range

**Findings:**
- Number of findings >= `expectedFindings`
- At least one finding matches `expectedCategories`
- If `expectedSeverity` is specified, at least one finding has that severity
- All findings have `confidence >= 80` (the default threshold)

**Special case: trivial-readme-fix**
- Should trigger the trivial diff fast path (< 5 meaningful lines)
- Output should be: "Trivial change. Risk: <score>/100. No findings."
- Risk score should be < 10

### 4. Record results

For each fixture, record:
```
Fixture: <name>
Risk Score: <actual> (expected: <range>)  [PASS/FAIL]
Findings: <count> (expected: >= <min>)    [PASS/FAIL]
Categories: <found> (expected: <list>)    [PASS/FAIL]
Severity: <found> (expected: <expected>)  [PASS/FAIL]
```

## Pass Criteria

- ALL 5 v1 fixtures must pass risk score range validation
- ALL 5 v1 fixtures must pass finding count validation
- ALL 5 v1 fixtures must have at least one finding matching the expected category
- Overall: 5/5 v1 fixtures passing = v1 suite passes

For the v2 fixtures, each must additionally pass:
- `tier0Verdict` matches the actual `TIER_ZERO_START..TIER_ZERO_END` block's `verdict:` field.
- `llmSwarmSkipped == true` implies zero agent dispatches in Step 4 (no FINDING_START blocks from review agents — Tier-0 findings only).
- `llmSwarmSkipped == false` implies the review swarm ran normally.
- `blockReason`, `confidenceThresholdBumpedTo`, `wiringChecksFailed` are asserted when present.

## Regression Testing

After modifying any agent prompt or risk scoring weights:
1. Re-run all 5 fixtures
2. Compare results against baseline
3. Flag any fixture where:
   - Risk score moved outside expected range
   - Expected findings no longer appear
   - Confidence dropped below 80 for expected findings
   - New false positives appeared (findings on trivial-readme-fix)
