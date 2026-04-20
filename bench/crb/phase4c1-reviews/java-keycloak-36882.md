Warning: consistency agent failed (4/5 agents completed)

## Summary
11 files changed, 70 lines added, 18 lines deleted. 5 findings (4 critical, 1 improvement, 0 nitpicks).
Exit code 4 reassigned — external consumers that previously interpreted exit code 4 as recreate-required will now silently mishandle the signal.

## Critical

:red_circle: [testing] testFeatureNotEnabled does not assert the FEATURE_DISABLED (4) exit code — the exit-code contract is untested in quarkus/tests/integration/src/test/java/org/keycloak/it/cli/dist/UpdateCommandDistTest.java:46 (confidence: 95)
The new `testFeatureNotEnabled` test only calls `cliResult.assertError(...)` on the stderr string. It never asserts `cliResult.exitCode() == CompatibilityResult.FEATURE_DISABLED`. Since the PR's whole premise is a new exit-code semantic (FEATURE_DISABLED = 4), and since the neighboring constant RECREATE_UPGRADE_EXIT_CODE was renumbered in this same PR, the absence of a numeric exit-code assertion is a glaring gap. A regression that returns 0/1/2 would pass this test.
```suggestion
@Test
@Launch({UpdateCompatibility.NAME, UpdateCompatibilityMetadata.NAME})
public void testFeatureNotEnabled(CLIResult cliResult) {
    cliResult.assertError("Unable to use this command. The preview feature 'rolling-updates' is not enabled.");
    assertEquals(CompatibilityResult.FEATURE_DISABLED, cliResult.exitCode());
}
```

:red_circle: [correctness] Exit code 4 reassigned — external consumers that previously interpreted exit code 4 as "recreate required" will now silently interpret every recreate as "feature disabled" in quarkus/runtime/src/main/java/org/keycloak/quarkus/runtime/compatibility/CompatibilityResult.java:30 (confidence: 95)
Before this PR, `RECREATE_UPGRADE_EXIT_CODE = 4`. After this PR, `RECREATE_UPGRADE_EXIT_CODE = 3` and `FEATURE_DISABLED = 4`. Any Kubernetes operator reconciler, CI pipeline, or shell script that checks for exit code 4 to decide "recreate upgrade needed" will now trigger on the feature-disabled misconfiguration case instead, and will get exit code 3 (previously undocumented in that role) for the recreate case. The two outcomes require opposite reactions. The in-code comment cites picocli's reserved codes (1 = software error, 2 = usage error) as justification, but picocli does not reserve code 3, so the renumbering is unforced. Since `update-compatibility` is PREVIEW the formal compat obligation is limited, but the silent swap is still a correctness hazard for every existing integration.
```suggestion
public interface CompatibilityResult {
    int ROLLING_UPGRADE_EXIT_CODE  = 0;
    int RECREATE_UPGRADE_EXIT_CODE = 4;  // unchanged — preserves the externally-visible exit code
    int FEATURE_DISABLED           = 5;  // new, distinct from every prior code
    // ...
}
```

:red_circle: [cross-file-impact] Exit-code documentation change is not accompanied by a migration note — silent contract change for external consumers in docs/guides/server/update-compatibility.adoc:128 (confidence: 90)
The docs table now lists exit codes 2, 3, 4 with code 4 meaning "feature disabled" and code 3 meaning "recreate required". Previously the Java constant `RECREATE_UPGRADE_EXIT_CODE` was 4, so any external tooling (operator reconcilers, GitOps scripts, CI) that branched on exit 4 will now silently fall through to unknown-code handling and permit a rolling upgrade in a case that required Recreate. No migration note, no CHANGELOG entry, no deprecation warning.
```suggestion
[NOTE]
====
The meaning of exit code `4` changed in this release. Previously exit code `4` indicated
"Recreate upgrade required"; it now indicates "feature `rolling-updates` is disabled".
Consumers parsing the exit code must audit their branching logic before upgrading.
====
```

:red_circle: [cross-file-impact] updatecompatibility macro unconditionally appends --features=rolling-updates to every rendered command — docs pages using the macro without a CAUTION admonition now silently teach users to pass a PREVIEW flag in docs/guides/templates/kc.adoc:52 (confidence: 85)
The macro previously rendered `bin/kc.[sh|bat] update-compatibility ${parameters}`. After the change, every call site gets `--features=rolling-updates` appended. The CAUTION admonition explaining the preview requirement was only added to two guides (`update-compatibility.adoc` and `advanced-configuration.adoc`); any other `.adoc` page consuming `<@tmpl.updatecompatibility>` now emits an example that silently assumes a preview feature. Users copy-pasting from those secondary pages will hit exit code 4 (feature disabled) with no on-page explanation.
```suggestion
<#macro updatecompatibility parameters features="rolling-updates">
[source,bash]
----
bin/kc.[sh|bat] update-compatibility ${parameters}<#if features?has_content> --features=${features}</#if>
----
</#macro>
```

## Improvements

:yellow_circle: [testing] No symmetric testFeatureNotEnabled coverage for UpdateCompatibilityCheck subcommand in quarkus/tests/integration/src/test/java/org/keycloak/it/cli/dist/UpdateCommandDistTest.java:46 (confidence: 90)
`testFeatureNotEnabled` only exercises the `UpdateCompatibilityMetadata` subcommand without the feature flag. `UpdateCompatibilityCheck` is a separate subclass that got the same guard, but no test verifies the guard fires on the check path. If a future change removes the guard from `UpdateCompatibilityCheck.run()` while leaving it in the metadata path, no test would catch it.
```suggestion
@Test
@Launch({UpdateCompatibility.NAME, UpdateCompatibilityCheck.NAME})
public void testFeatureNotEnabledOnCheck(CLIResult cliResult) {
    cliResult.assertError("Unable to use this command. The preview feature 'rolling-updates' is not enabled.");
    assertEquals(CompatibilityResult.FEATURE_DISABLED, cliResult.exitCode());
}
```

## Risk Metadata
Risk Score: 31/100 (MEDIUM) | Blast Radius: Profile.java is a widely-imported core enum; CompatibilityResult exit codes are an external process contract | Sensitive Paths: none
AI-Authored Likelihood: LOW

(2 additional findings below confidence threshold)
