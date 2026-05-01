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

### Stacked-PR fixtures (PR #92 — `--parent` / `--parent-sha` / `--stack-auto`)

Exercise the Mode B stacked-PR orchestrator wiring landed in `skills/pr-review/SKILL.md` Step 1 Mode B step 4. End-to-end /pr-review-driven assertion is auth-gated; structural validation runs unconditionally.

| Fixture | Flag | Notes |
|---------|------|-------|
| stacked-pr-basic | (no flag — use `/pr-review --parent <N>` or `--parent-sha <SHA>` or `--stack-auto`) | Child PR adds `revoke_session()` to src/auth/session.py; parent PR (referenced via `--parent N`) adds `now: Date` parameter to `validate_session()`. Diff scoping must be `git diff <PARENT_HEAD>...pr-<N>` (delta-only, NOT base-vs-head). Risk score MEDIUM (auth/ sensitive-paths hit + new function in security-sensitive area). |

Each stacked-PR fixture's `expected.json` carries: `stackParentRequired` (bool), `stackParentMetadata` (dict with `pr`/`headSha`/`title`), `diffScopedAgainst` (string describing scoping), `prDescriptionAugmented` (bool), `expectedPrDescriptionPrefix` (string), `agentTriggerHint` (string — the canonical "don't flag missing function from parent PR" scenario from `rules/stacked-pr-mode.md` § PR-description augmentation).

### Phase 4b fixtures (hallucination-AST pre-check)

End-to-end tests for the `lib/hallucination-ast/` package integration via `agents/hallucination.md` §2.5. Each fixture's `expected.json` carries a `phase4bExpected` block with the exact rule + symbol + confidence the deterministic pre-check must emit.

| Fixture | Target | Expected Rule | Category | Severity |
|---------|--------|---------------|----------|----------|
| hallucinated-import | Python `requests.gett(url)` typo | `identifier_not_found` with `suggestedFix: "get"` | hallucination | critical |
| signature-mismatch | Python `os.makedirs(path, recursive=True)` — JS→Python cross-language hallucination | `signature_mismatch_keyword` for `recursive` | hallucination | improvement |

Both fixtures are Python-only (v0.1 backend). Non-Python diffs skip the pre-check and fall through to the LLM pipeline.

To validate manually:

```bash
python -m hallucination_ast --diff tests/fixtures/hallucinated-import/diff.patch | jq .findings
python -m hallucination_ast --diff tests/fixtures/signature-mismatch/diff.patch | jq .findings
```

Each must produce exactly one finding matching the `phase4bExpected` block, at `confidence: 100`.

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
- `tier0Verdict` matches the actual `TIER_ZERO_START..TIER_ZERO_END` block's `verdict:` field
  when the field is present in `expected.json`. Fixtures whose primary assertion is NOT the
  Tier-0 verdict (e.g., `spec-alignment-unmet-checklist`) may omit `tier0Verdict` — the v2
  table's `—` in the verdict column indicates this intentional omission.
- `llmSwarmSkipped == true` implies zero agent dispatches in Step 4 (no FINDING_START blocks
  from review agents — Tier-0 findings only).
- `llmSwarmSkipped == false` implies the review swarm ran normally.
- `blockReason`, `confidenceThresholdBumpedTo`, `wiringChecksFailed`, and
  `expectedTier0FindingCategory` are asserted when present.
- v2 fixtures inherit the v1 `riskRange` assertion: actual risk score must fall within
  `riskRange[0]..riskRange[1]` inclusive.

## Automated coverage (partial — POST_V2_FOLLOWUPS §G2)

Two of the three runner modes are now wired. CI workflow at `.github/workflows/fixture-runner.yml` runs them on every PR / push that touches the fixtures, the runner script, or `lib/hallucination-ast/`:

| Mode | Coverage | Auth needed? |
|---|---|---|
| `structural` | All 16 fixtures: validates `diff.patch` non-empty + `expected.json` schema (riskRange shape, expectedFindings type, severity allowed values, optional v2 fields including Tier-0, phase4b, and stacked-PR field type-checks). | None |
| `phase4b` | The 2 phase4b fixtures (`hallucinated-import`, `signature-mismatch`): subprocesses `python -m hallucination_ast --diff <fixture>/diff.patch`, asserts the emitted finding matches `phase4bExpected.rule` + `.symbol` (+ `.suggestedFix` / `.confidence` when present). The CLI exits 1 on CRITICAL findings by design (fail-loud CI convention); the runner parses stdout regardless of exit code. | None — uses local CLI only |

Run locally:

```bash
python tests/run_fixtures.py --mode all          # both modes (default)
python tests/run_fixtures.py --mode structural   # quick file/schema check
python tests/run_fixtures.py --mode phase4b      # exercise hallucination-ast CLI
```

## Deferred

The full /pr-review-driven runner — asserting risk ranges, finding counts, expected categories, severity bands, and stacked-PR diff scoping / prDescription augmentation across all 16 fixtures — is still deferred. It requires Anthropic API auth in CI (same blocker as the Soliton-Review dogfood workflow). When `ANTHROPIC_API_KEY` (or the OAuth-token equivalent) lands in repo secrets, this runner can grow a `--mode pr-review` arm that subprocesses Claude Code with `--plugin-dir .` and parses the markdown output's emoji-prefixed finding lines.

Additional deferred items tracked for the same follow-up:

- `tier0-blocked-cve/` fixture (OSV-scanner critical CVE → `blockReason: cve_critical`)
- `tier0-blocked-type-error/` fixture (tsc/mypy fatal type error → `blockReason: type_error_fatal`)
- `tier0-needs-llm/` fixture (non-trivial clean diff that routes through the default path)
- `.gitleaksignore` entry or inline `# gitleaks:allow` annotation so consumers who run
  full-tree scans don't false-positive on the `tier0-blocked-secret` fixture.

## Regression Testing

After modifying any agent prompt or risk scoring weights:
1. Re-run all 5 fixtures
2. Compare results against baseline
3. Flag any fixture where:
   - Risk score moved outside expected range
   - Expected findings no longer appear
   - Confidence dropped below 80 for expected findings
   - New false positives appeared (findings on trivial-readme-fix)
