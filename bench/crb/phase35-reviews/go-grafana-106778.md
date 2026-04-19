## Summary
14 files changed, 542 lines added, 418 lines deleted. 2 findings (2 critical, 0 improvements, 0 nitpicks).
Three new exported hooks in useAbilities.ts and a new GrafanaRuleListItem component landed without dedicated unit tests.

## Critical

:red_circle: [testing] Three new exported hooks have no unit tests in public/app/features/alerting/unified/hooks/useAbilities.ts:452 (confidence: 92)
useAllGrafanaPromRuleAbilities, useGrafanaPromRuleAbility, and useGrafanaPromRuleAbilities are newly introduced and contain substantial logic: useIsGrafanaPromRuleEditable with three distinct states, MaybeSupported unconditionally set to AlwaysSupported (unlike the ruler-gated version it replaces), and skipToken sentinel paths. Existing coverage in RulesTable.test.tsx mocks all ability hooks at the boundary — no actual hook behavior is exercised. Provisioning immutability, permission gating, and loading-state logic have zero direct test coverage.
```suggestion
Add public/app/features/alerting/unified/hooks/useAbilities.test.ts covering:
(1) undefined rule returns [false, false] on all abilities,
(2) provisioned rule is correctly treated as immutable,
(3) skipToken paths return [false, false],
(4) recording-rule pause/silence gating is correctly blocked.
```

:red_circle: [testing] New GrafanaRuleListItem component has no dedicated test file in public/app/features/alerting/unified/rule-list/GrafanaRuleListItem.tsx:1 (confidence: 88)
GrafanaRuleListItem.tsx is a new production file extracted from the deleted GrafanaRuleLoader.tsx. It contains branching logic across three rule types (alerting, recording, unknown), href construction from rule.uid, and new reads of health/error/labels/provenance/isPaused from the prom DTO (not the ruler rule). The actions prop is wired to pass only promRule. The old GrafanaRuleLoader was tested indirectly via GrafanaGroupLoader.test.tsx, but the extracted component's type-dispatch logic, href construction, and action button wiring are entirely uncovered.
```suggestion
Add GrafanaRuleListItem.test.tsx covering:
(1) alerting rule href construction from rule.uid,
(2) recording rule rendering (no state column, no instance count),
(3) provisioned badge rendering when provenance is present.
```

## Risk Metadata
Risk Score: 41/100 (MEDIUM) | Blast Radius: useAbilities.ts imported by ~6 files visible in diff and likely dozens in full codebase; rules.ts and unified-alerting-dto.ts are broad utility/type files | Sensitive Paths: none
AI-Authored Likelihood: LOW

(10 additional findings below confidence threshold)
