# Test Fixture Validation Guide

This document describes how to validate the Soliton PR Review skill against the test fixture corpus.

## Fixtures

| Fixture | Expected Risk | Expected Findings | Expected Category | Expected Severity |
|---------|--------------|-------------------|-------------------|-------------------|
| trivial-readme-fix | 0-10 | 0 | (none) | (none) |
| sql-injection | 60-100 | 1+ | security | critical |
| hallucinated-api | 30-80 | 1+ | hallucination | critical |
| missing-tests | 30-70 | 1+ | testing | improvement+ |
| cross-file-break | 50-90 | 1+ | cross-file-impact | critical |

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

- ALL 5 fixtures must pass risk score range validation
- ALL 5 fixtures must pass finding count validation
- ALL 5 fixtures must have at least one finding matching the expected category
- Overall: 5/5 fixtures passing = suite passes

## Regression Testing

After modifying any agent prompt or risk scoring weights:
1. Re-run all 5 fixtures
2. Compare results against baseline
3. Flag any fixture where:
   - Risk score moved outside expected range
   - Expected findings no longer appear
   - Confidence dropped below 80 for expected findings
   - New false positives appeared (findings on trivial-readme-fix)
