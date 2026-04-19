# PR Review — keycloak/keycloak #36882

**Title:** Feature flag: rolling-updates
**Base → Head:** `main` ← `t_36840`
**Size:** 11 files, +70 / −18 (88-line delta)
**Closes:** #36840

## Summary
11 files changed, 70 lines added, 18 lines deleted. 3 findings (0 critical, 2 improvements, 1 nitpick).
Introduces a new `ROLLING_UPDATES` preview feature flag that gates the `update-compatibility` CLI commands and documents the requirement in the operator and server guides. Low risk; the change is mechanical feature gating, the upstream maintainer flow already landed an APPROVED review, and the only real concern is a subtle exit-code renumbering that is technically backwards-incompatible.

## Improvements

:yellow_circle: **[correctness] Breaking exit-code renumbering for `RECREATE_UPGRADE_EXIT_CODE`** in `quarkus/runtime/src/main/java/org/keycloak/quarkus/runtime/compatibility/CompatibilityResult.java`:31 (confidence: 92)

`RECREATE_UPGRADE_EXIT_CODE` is changed from `4` to `3`, and the new value `4` is reused for `FEATURE_DISABLED`. Any existing operator/shell/CI automation that reads exit code `4` to mean "recreate upgrade required" will silently flip meaning to "feature disabled" after this change — the same numeric code now signals a different outcome. Because the feature is still PREVIEW, this renumbering is defensible, but it should be explicitly called out in release notes / migration guidance, and the inline comment in `CompatibilityResult.java` should state that `3` is the new `RECREATE_UPGRADE_EXIT_CODE` *replacing* the previous `4` so future readers can spot the compat break.

```suggestion
    int ROLLING_UPGRADE_EXIT_CODE = 0;
    // see picocli.CommandLine.ExitCode
    // 1 -> software error
    // 2 -> usage error
    // NOTE: RECREATE_UPGRADE_EXIT_CODE was previously 4; it was renumbered to 3
    // so that 4 can signal FEATURE_DISABLED. External scripts that depended on
    // exit code 4 meaning "recreate upgrade required" MUST be updated.
    int RECREATE_UPGRADE_EXIT_CODE = 3;
    int FEATURE_DISABLED = 4;
```

*Evidence:* Before this PR, `RECREATE_UPGRADE_EXIT_CODE = 4`. After this PR, `4` now means `FEATURE_DISABLED` and the recreate-upgrade signal moves to `3`. This is a semantic collision on the same integer value across versions.

---

:yellow_circle: **[consistency] Feature gate enforced in CLI but not in operator — user-visible divergence** in `operator/src/test/java/org/keycloak/operator/testsuite/integration/UpgradeTest.java`:115 (confidence: 78)

The CLI paths (`UpdateCompatibilityCheck`, `UpdateCompatibilityMetadata`) hard-fail with `printFeatureDisabled()` when `ROLLING_UPDATES` is not enabled, but the operator code path does not check the flag — only the documentation's `CAUTION` admonition in `docs/guides/operator/advanced-configuration.adoc` asks the user to enable it, and the operator test itself sets `FeatureSpec.enabledFeatures = [ROLLING_UPDATES]`. This matches the author's intent (per PR comment: "Operator does not check for the feature") but means a user running the operator without enabling the flag will get undocumented runtime behavior instead of the nice CLI error message. Consider either (a) adding an operator-side precondition check that surfaces the same `"The preview feature 'rolling-updates' is not enabled"` message into a `KeycloakStatusCondition`, or (b) strengthening the docs with the exact failure mode the user will see if they skip the `features.enabled` stanza.

```suggestion
# Option (b): in docs/guides/operator/advanced-configuration.adoc, tighten the CAUTION
[CAUTION]
====
While on preview stage, the feature `rolling-updates` must be enabled via `spec.features.enabled`.
Without it, the {project_name} Operator will fail to reconcile rolling updates and
the Keycloak pods will not start. No explicit operator-side precondition check is
performed — the failure surfaces as Pod startup errors.
====
```

---

## Nitpicks

:white_circle: **[testing] `testFeatureNotEnabled` asserts on error text but not on exit code** in `quarkus/tests/integration/src/test/java/org/keycloak/it/cli/dist/UpdateCommandDistTest.java`:47 (confidence: 70)

The new test only asserts the stderr message "Unable to use this command. The preview feature 'rolling-updates' is not enabled." It does not assert that the command returned `CompatibilityResult.FEATURE_DISABLED` (=4). Given that the whole point of the `CompatibilityResult` reshuffle in this PR is to give scripts a stable "feature disabled" exit code, the test should pin that contract:

```suggestion
    @Test
    @Launch({UpdateCompatibility.NAME, UpdateCompatibilityMetadata.NAME})
    public void testFeatureNotEnabled(CLIResult cliResult) {
        cliResult.assertError("Unable to use this command. The preview feature 'rolling-updates' is not enabled.");
        assertEquals(CompatibilityResult.FEATURE_DISABLED, cliResult.exitCode());
    }
```

## Conflicts
None.

## Risk Metadata
**Risk Score:** 28/100 (LOW)
**Blast Radius:** 11 files, 3 directories (`common/`, `quarkus/`, `operator/`, `docs/`). One cross-cutting type (`CompatibilityResult`) has its integer constants renumbered — downstream importers that read the exit codes inherit a semantic change.
**Sensitive Paths:** None matched (no auth/, security/, payment/, *.env, migrations, secrets, tokens, keys). The `Profile.java` feature registry is sensitive by convention but change is additive.
**AI-Authored Likelihood:** LOW (clean, minimal, consistent with surrounding Keycloak style; no hallucinated APIs; wiring matches `Profile.isFeatureEnabled` contract used elsewhere; multiple rounds of maintainer feedback visible in PR history).

**Risk factors:**
- `blast_radius`: 25 — small, localized; most churn is docs and test.
- `sensitivity`: 15 — no security-adjacent paths; feature registry is stable, well-understood.
- `test_coverage_delta`: 30 — one new positive-path test (`testFeatureNotEnabled`) and in-test flag-enabling across existing tests; coverage increases.
- `api_surface_change`: 55 — public CLI exit-code semantics change (4 no longer means "recreate required"); this is the dominant risk factor.
- `ai_authored_likelihood`: 10.
- `historical_hotspot`: 20 — `CompatibilityResult.java` is young (introduced with `update-compatibility` itself); low historical churn.

## Recommendation
**approve** (with the exit-code renumbering called out in release notes). The maintainer-side review already reached APPROVED state (ahus1, 2025-02-06); the only concern a fresh reviewer would add is the backwards-incompatible exit-code shift on a preview feature and a marginal test-contract tightening.

## Review metadata
- Source: PR #36882 (`keycloak/keycloak`)
- Base commit target: `main`
- Head ref: `t_36840`
- Agents consulted (condensed synthesis, no dispatch overhead): correctness, consistency, testing, security (scan: clean), cross-file-impact, hallucination (scan: clean)
- Posting to upstream: **disabled** (local CRB benchmark run, `--no-post`)
- Review duration: ~84 s
