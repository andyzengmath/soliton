## Summary
11 files changed, 70 lines added, 18 lines deleted. 3 findings (1 critical, 2 improvements, 0 nitpicks).
Preview feature gate `rolling-updates` wired through CLI commands and operator tests; an exit-code renumbering inside `CompatibilityResult` is a silent contract change for any consumer of the previous value.

## Critical
:red_circle: [correctness] Breaking change to `RECREATE_UPGRADE_EXIT_CODE` (4 → 3) will silently mis-classify external callers in `quarkus/runtime/src/main/java/org/keycloak/quarkus/runtime/compatibility/CompatibilityResult.java:30` (confidence: 92)
The existing contract emitted exit code `4` for "recreate upgrade required"; this PR repurposes `4` as `FEATURE_DISABLED` and renumbers recreate to `3`. Any CI/automation that branches on `exit == 4` (the only non-zero code this interface had documented) will now interpret a feature-disabled run as a successful recreate decision and proceed with a disruptive restart, or vice-versa.
```suggestion
    int ROLLING_UPGRADE_EXIT_CODE = 0;
    // see picocli.CommandLine.ExitCode
    // 1 -> software error
    // 2 -> usage error
    int RECREATE_UPGRADE_EXIT_CODE = 4;
    int FEATURE_DISABLED_EXIT_CODE = 5;
```
<details><summary>More context</summary>

The `update-compatibility` command was introduced as preview, so a strict semver argument is weaker here, but the exit code is the command's only machine-readable output and has been publicly documented since it landed (see `docs/guides/server/update-compatibility.adoc` table). Keeping `4` stable and assigning the new signal a fresh integer preserves backward compatibility for any operator/CI pipeline that already integrated with the preview. If renumbering is intentional, the PR should ship a CHANGELOG/release-notes entry explicitly calling out the exit-code migration rather than only updating the table in the adoc.
</details>

## Improvements
:yellow_circle: [consistency] `FEATURE_DISABLED` constant name omits the `_EXIT_CODE` suffix shared by its siblings in `quarkus/runtime/src/main/java/org/keycloak/quarkus/runtime/compatibility/CompatibilityResult.java:32` (confidence: 88)
`ROLLING_UPGRADE_EXIT_CODE` and `RECREATE_UPGRADE_EXIT_CODE` both encode the "is an exit code" semantic in the name; the new `FEATURE_DISABLED` constant does not, making call sites ambiguous. Rename for grep-ability and to avoid future confusion with non-exit-code feature flags.
```suggestion
    int FEATURE_DISABLED_EXIT_CODE = 4;
```

:yellow_circle: [correctness] `printFeatureDisabled()` error message does not tell the operator how to enable the feature in `quarkus/runtime/src/main/java/org/keycloak/quarkus/runtime/cli/command/AbstractUpdatesCommand.java:83` (confidence: 75)
The message `"Unable to use this command. The preview feature 'rolling-updates' is not enabled."` leaves the user to guess the remediation; every other call site in this PR passes `--features=rolling-updates`, so the fix is trivial to suggest inline. Including the flag in the error lowers the support cost for a preview feature.
```suggestion
        printError("Unable to use this command. The preview feature 'rolling-updates' is not enabled. Start with '--features=rolling-updates' or set it in keycloak.conf to enable.");
```

## Risk Metadata
Risk Score: 45/100 (MEDIUM) | Blast Radius: 2 CLI entry points + 1 public exit-code contract + operator test wiring | Sensitive Paths: none (no auth/security/secret paths touched)
AI-Authored Likelihood: LOW

## Review Metadata
- Source: PR #36882 (keycloak/keycloak)
- Base: `main` ← Head: `t_36840`
- Recommendation: request-changes (exit-code renumbering needs explicit review or revert before merge)
- Agents: direct analysis (diff small enough to bypass chunked swarm)
