## Summary
11 files changed, 70 lines added, 18 lines deleted. 1 finding (0 critical, 1 improvement, 0 nitpicks).
Introduces a `ROLLING_UPDATES` preview feature flag that gates the `update-compatibility` CLI commands and updates operator/server docs. Mechanical feature-gating; the one material concern is a backwards-incompatible exit-code renumbering on `CompatibilityResult`.

## Improvements

:yellow_circle: [correctness] Breaking exit-code renumbering for `RECREATE_UPGRADE_EXIT_CODE` in `quarkus/runtime/src/main/java/org/keycloak/quarkus/runtime/compatibility/CompatibilityResult.java`:31 (confidence: 92)
`RECREATE_UPGRADE_EXIT_CODE` changes from `4` to `3`, and the newly introduced `FEATURE_DISABLED` reuses `4`. Any existing operator/shell/CI automation that reads exit code `4` to mean "recreate upgrade required" will silently flip meaning to "feature disabled" after this change — the same numeric code now signals a different outcome across versions. Because the feature is still PREVIEW, this renumbering is defensible, but it should be called out in release notes/migration guidance, and the inline comment should state that `3` is the new `RECREATE_UPGRADE_EXIT_CODE` *replacing* the previous `4` so future readers can spot the compat break.
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
*Evidence:* Before this PR, `RECREATE_UPGRADE_EXIT_CODE = 4`. After this PR, `4` now means `FEATURE_DISABLED` and the recreate-upgrade signal moves to `3`. This is a semantic collision on the same integer value across versions and is directly observable by any CI/automation that branches on `$?`.

## Risk Metadata
Risk Score: 28/100 (LOW) | Blast Radius: 11 files across `common/`, `quarkus/`, `operator/`, `docs/`; one cross-cutting type (`CompatibilityResult`) has its integer constants renumbered — downstream importers that read the exit codes inherit a semantic change | Sensitive Paths: none matched (no `auth/`, `security/`, `payment/`, `*.env`, migrations, secrets, tokens, keys); `Profile.java` feature registry is sensitive by convention but the change is strictly additive
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold: operator feature-gate divergence [consistency, conf 78]; `testFeatureNotEnabled` missing exit-code assertion [testing, conf 70])
