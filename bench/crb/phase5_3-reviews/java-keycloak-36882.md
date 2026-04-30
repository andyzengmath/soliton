## Summary
11 files changed, 70 lines added, 18 lines deleted. 3 findings (0 critical, 3 improvements, 0 nitpicks).
Introduces a `rolling-updates` PREVIEW feature flag and gates the `update-compatibility` CLI commands behind it; the RECREATE exit code is renumbered (4→3) to free exit code 4 for the new "feature disabled" signal.

## Improvements

:yellow_circle: [cross-file-impact] Backwards-incompatible exit-code renumbering for `RECREATE_UPGRADE_EXIT_CODE` (4 → 3) in `quarkus/runtime/src/main/java/org/keycloak/quarkus/runtime/compatibility/CompatibilityResult.java`:31 (confidence: 90)
The PR changes `RECREATE_UPGRADE_EXIT_CODE` from `4` to `3` and reassigns `4` to the new `FEATURE_DISABLED` sentinel. Any operator scripts, Helm charts, GitOps pipelines, or CI logic that branched on the old `update-compatibility check` exit code `4` to mean "recreate upgrade required" will silently change behavior — exit code `4` now means "feature is disabled" (a configuration mistake), not "recreate is needed" (an expected outcome). Even though the feature is PREVIEW, the previous exit-code contract was already documented (`docs/guides/server/update-compatibility.adoc` previously listed `4 → recreate required`) and external consumers may have wired against it during the preview period. Consider keeping `RECREATE_UPGRADE_EXIT_CODE = 4` and assigning a different value (e.g. `5`) to `FEATURE_DISABLED` to preserve the existing contract; if the renumber is intentional, call it out explicitly in release notes / upgrade guide so preview adopters can react.
```suggestion
    int ROLLING_UPGRADE_EXIT_CODE = 0;
    // see picocli.CommandLine.ExitCode
    // 1 -> software error
    // 2 -> usage error
    int RECREATE_UPGRADE_EXIT_CODE = 4;
    int FEATURE_DISABLED = 5;
```

:yellow_circle: [consistency] `Dockerfile-custom-image` unconditionally bakes `--features=rolling-updates` into every custom image in `operator/scripts/Dockerfile-custom-image`:5 (confidence: 80)
The custom-image Dockerfile is the documented escape hatch for users who want to extend Keycloak with extra build options (additional providers, themes, etc.) — it is not specific to rolling updates. Hard-wiring `--features=rolling-updates` here forces the preview feature on for *every* downstream user who derives from this template, even those who never use the rolling-update CLI. Because `Profile.Feature.ROLLING_UPDATES` is `Type.PREVIEW`, this also implicitly opts those users into preview surface area. Prefer leaving the build line minimal and instead documenting in `docs/guides/operator/advanced-configuration.adoc` (which already recommends enabling the feature in the Keycloak CR) that operators who need the CLI must add `--features=rolling-updates` to their own derived Dockerfile.
```suggestion
RUN /opt/keycloak/bin/kc.sh build --db=postgres --health-enabled=true
```

:yellow_circle: [correctness] Unrelated removal of `UnsupportedSpec` initialization mixed into the feature-flag change in `operator/src/test/java/org/keycloak/operator/testsuite/integration/UpgradeTest.java`:110-115 (confidence: 70)
The diff replaces the `if (kc.getSpec().getUnsupported() == null) { kc.getSpec().setUnsupported(new UnsupportedSpec()); }` block with a `FeatureSpec` initialization. This is semantically distinct from "enable rolling-updates" — `UnsupportedSpec` is removed entirely from this test even though no other change in the PR justifies dropping it. If `UnsupportedSpec` was being initialized for a reason (e.g. defaults required by the operator reconciler), the test may now silently exercise a different code path than before. Either restore the `UnsupportedSpec` initialization alongside the new `FeatureSpec` setup, or add a brief comment / commit message note confirming the `UnsupportedSpec` initialization was provably dead.
```suggestion
        var updateSpec = new UpdateSpec();
        updateSpec.setStrategy(updateStrategy);
        kc.getSpec().setUpdateSpec(updateSpec);

        if (kc.getSpec().getUnsupported() == null) {
            kc.getSpec().setUnsupported(new UnsupportedSpec());
        }
        if (kc.getSpec().getFeatureSpec() == null) {
            kc.getSpec().setFeatureSpec(new FeatureSpec());
        }
        kc.getSpec().getFeatureSpec().setEnabledFeatures(List.of(Profile.Feature.ROLLING_UPDATES.getKey()));
        return kc;
```

## Risk Metadata
Risk Score: 28/100 (LOW) | Blast Radius: 11 files across docs, common, quarkus runtime, operator tests, integration tests; touches CLI exit-code contract (semi-public) | Sensitive Paths: none
AI-Authored Likelihood: LOW
