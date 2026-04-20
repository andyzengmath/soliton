## Summary
11 files changed, 70 lines added, 18 lines deleted. 2 findings (1 critical, 1 improvement).
Gates `update-compatibility` CLI and the Operator's rolling-updates path behind a new `ROLLING_UPDATES` preview feature flag; renumbers the exit-code contract of the CLI while doing so.

## Critical
:red_circle: [cross-file-impact] Backward-incompatible exit-code renumbering on a public CLI contract in quarkus/runtime/src/main/java/org/keycloak/quarkus/runtime/compatibility/CompatibilityResult.java:30 (confidence: 92)
`RECREATE_UPGRADE_EXIT_CODE` is changed from `4` → `3`, and the value `4` is re-used for the new `FEATURE_DISABLED`. The `kc.sh update-compatibility …` commands are the documented integration surface for operators/CI that decide between rolling vs. recreate upgrades (see `docs/guides/server/update-compatibility.adoc` — the table at `m|3` / `m|4` is itself updated in this PR, confirming the values are an external contract, not an internal detail). Any consumer that previously branched on `exit == 4 ⇒ recreate` — Helm post-install hooks, Ansible/GitOps pipelines, the Keycloak Operator’s own update-reconciliation logic, or customer runbooks built against the preview — will now silently mis-classify: with the feature enabled, a real "recreate required" result returns `3` (often ignored as "unknown non-zero" or treated as failure); with the feature disabled, `4` is returned and will be interpreted as "recreate required" even though the command never actually ran the compatibility check. The resulting false-positive "rolling upgrade OK" or false-negative "recreate needed" is exactly the failure mode this tool exists to prevent. The preview label mitigates but does not eliminate this: several public docs/blogs already document `4 = recreate` for Keycloak 26.x nightlies. If the renumbering is intentional, it must be called out explicitly in migration notes and the Operator's consumer code must be audited; otherwise, keep `RECREATE_UPGRADE_EXIT_CODE = 4` and assign `FEATURE_DISABLED` a fresh value.
```suggestion
    int ROLLING_UPGRADE_EXIT_CODE = 0;
    // see picocli.CommandLine.ExitCode
    // 1 -> software error
    // 2 -> usage error
    int FEATURE_DISABLED = 3;
    int RECREATE_UPGRADE_EXIT_CODE = 4;
```
[References: docs/guides/server/update-compatibility.adoc (exit-code table), https://picocli.info/apidocs/picocli/CommandLine.ExitCode.html]

## Improvements
:yellow_circle: [consistency] Feature-disabled error message does not tell the user how to enable the feature in quarkus/runtime/src/main/java/org/keycloak/quarkus/runtime/cli/command/AbstractUpdatesCommand.java:83 (confidence: 86)
`printFeatureDisabled()` emits "Unable to use this command. The preview feature 'rolling-updates' is not enabled." with no actionable remediation. Every documentation template in this same PR (`docs/guides/templates/kc.adoc`, `operator/scripts/Dockerfile-custom-image`, `docs/guides/operator/advanced-configuration.adoc`) tells the user the enable path is `--features=rolling-updates` — the CLI itself should mirror that guidance so the operator running the failing command does not need to cross-reference docs to recover. This is especially important because the failure is exit-code `4`, which (per the finding above) is easy to confuse with "recreate required" in automated contexts — a self-describing error message reduces that ambiguity.
```suggestion
    void printFeatureDisabled() {
        printError("Unable to use this command. The preview feature 'rolling-updates' is not enabled. Enable it by passing --features=rolling-updates (or set kc.features=rolling-updates in keycloak.conf).");
    }
```

## Risk Metadata
Risk Score: 38/100 (MEDIUM) | Blast Radius: CLI exit-code contract consumed by Operator + external automation; 11 files; preview feature gate added to 2 CLI entry points and 1 operator test path | Sensitive Paths: none (no auth/crypto/secret surfaces touched)
AI-Authored Likelihood: LOW
