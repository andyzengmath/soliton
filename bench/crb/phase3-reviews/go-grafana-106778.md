Warning: risk-scorer and correctness agents timed out (5/7 agents completed)

## Summary
14 files changed, 542 lines added, 418 lines deleted. 21 findings (3 critical, 13 improvements, 5 nitpicks).
Grafana alerting list-view refactor removes ruler API calls in favor of Prometheus API data. Introduces new batch ability hooks and an OR-combined permission gate, but several type-widening / signature changes are inconsistent across call sites, and the new Prom-based ability derivation likely misuses a ruler-only helper (`isPluginProvidedRule`).

## Critical

:red_circle: [cross-file-impact] `rule` prop passed as `undefined` to `useRulerRuleAbility` which may not accept it in `public/app/features/alerting/unified/rule-list/components/RuleActionsButtons.V2.tsx`:48 (confidence: 92)
`RuleActionsButtons.V2.tsx` widened `rule` to `RulerRuleDTO | undefined` via `RequireAtLeastOne`, but the singular `useRulerRuleAbility(rule, groupIdentifier, AlertRuleAction.Update)` call site was not updated and `useRulerRuleAbility`'s signature was NOT widened in this PR (only the plural `useRulerRuleAbilities` was). This is a compile-time type error; runtime behavior depends on whether the inner call passes `undefined` through safely.
```suggestion
// Either widen useRulerRuleAbility to accept RulerRuleDTO | undefined
// (matching useRulerRuleAbilities) or guard:
const [editRuleSupported, editRuleAllowed] = rule
  ? useRulerRuleAbility(rule, groupIdentifier, AlertRuleAction.Update)
  : [false, false];
```

:red_circle: [cross-file-impact] `useGrafanaRulesSilenceSupport` and `useCanSilenceInFolder` referenced but no matching import added in diff in `public/app/features/alerting/unified/hooks/useAbilities.ts`:455 (confidence: 88)
The new `useAllGrafanaPromRuleAbilities` function calls `useGrafanaRulesSilenceSupport()` and `useCanSilenceInFolder(rule?.folderUid)`. Neither appears in any added import line. If they are not already present in the pre-existing (unchanged) import section, this is an unresolved reference that will fail compilation. Verify the existing imports include these hooks before merging.
```suggestion
// Confirm (reading lines 1–13 of useAbilities.ts) that these are imported:
import { useGrafanaRulesSilenceSupport, useCanSilenceInFolder } from '...';
```

:red_circle: [testing] `useAllGrafanaPromRuleAbilities` and derived hooks have no direct unit tests in `public/app/features/alerting/unified/hooks/useAbilities.ts`:453 (confidence: 92)
The new hook contains substantial branching: `MaybeSupported = loading ? NotSupported : AlwaysSupported`, gating Pause/Restore/Silence/ModifyExport on `isAlertingRule`, suppressing writes when `isProvisioned` or `isPluginProvided`, and delegating editability to `useIsGrafanaPromRuleEditable`. `RulesTable.test.tsx` mocks the entire `useAbilities` module, so the implementation is never executed. Bugs in the provisioning guard or `isAlertingRule` gate would not be caught by any test.
```suggestion
// Add useAbilities.grafanaPromRule.test.ts
it('returns [false, false] for Update when rule is provisioned', () => {
  const rule = mockGrafanaPromAlertingRule({ provenance: 'terraform', folderUid: 'f1' });
  const { result } = renderHook(() => useGrafanaPromRuleAbility(rule, AlertRuleAction.Update));
  expect(result.current[0]).toBe(false);
});
it('returns [false, false] for all actions when skipToken is passed', () => {
  const { result } = renderHook(() => useGrafanaPromRuleAbility(skipToken, AlertRuleAction.Update));
  expect(result.current).toEqual([false, false]);
});
```

## Improvements

:yellow_circle: [hallucination] `isPluginProvidedRule` called with `GrafanaPromRuleDTO` instead of `RulerRuleDTO` in `public/app/features/alerting/unified/hooks/useAbilities.ts`:473 (confidence: 72)
In `useAllGrafanaPromRuleAbilities`, `isPluginProvidedRule(rule)` is called where `rule: GrafanaPromRuleDTO | undefined`. The sibling ruler hook uses the same helper against a `RulerRuleDTO`. The helper inspects ruler-specific fields (grafana_alert.provenance / labels) that `GrafanaPromRuleDTO` does not carry — so it will likely return `false` regardless, silently granting edit/delete on plugin-provided rules.
```suggestion
// Add prom-variant helper and call it here
const isPluginProvided = rule ? isPluginProvidedPromRule(rule) : false;
```

:yellow_circle: [cross-file-impact] `GrafanaRuleListItem` passes only `promRule` to `RuleActionsButtons` — `AlertRuleMenu` receives `rulerRule=undefined` in `public/app/features/alerting/unified/rule-list/GrafanaRuleListItem.tsx`:1 (confidence: 85)
List-view rendering intentionally omits `rulerRule`. `useRulerRuleAbilities` was widened to tolerate `undefined`, but reviewers must audit `AlertRuleMenu` for any branch dereferencing `rulerRule` (share-link, export flows) without a null guard in this path.
```suggestion
// In AlertRuleMenu, audit every rulerRule usage for null-safety;
// document the intentional omission:
// GrafanaRuleListItem deliberately omits rulerRule because the list view
// no longer fetches the ruler API. All ruler-based abilities will return
// [false, false], and the OR with grafanaPromRule abilities provides the UI gate.
```

:yellow_circle: [cross-file-impact] `useAllAlertRuleAbilities` marked `@deprecated` but federated-rule check silently dropped in `public/app/features/alerting/unified/hooks/useAbilities.ts`:388 (confidence: 80)
The new `useAllRulerRuleAbilities` replaces the explicit `isFederatedRuleGroup(rule.group)` check with `const isFederated = false` (commented TODO). Any caller of the deprecated `useAllAlertRuleAbilities` that relied on federated-rule enforcement will now permit edit/delete on federated rules.
```suggestion
// Restore the federated-rule guard or migrate all callers first
const isFederated = groupIdentifier.kind === 'prometheus'
  ? isFederatedRuleGroup(groupIdentifier)
  : false;
```

:yellow_circle: [cross-file-impact] Removed "Creating"/"Deleting" transient state indicators for Grafana-managed rules in `public/app/features/alerting/unified/rule-list/GrafanaGroupLoader.tsx`:65 (confidence: 75)
The prior implementation matched ruler-only rules to render `RuleOperation.Creating` and prom-only rules to render `RuleOperation.Deleting`. Removing the ruler fetch removes these transient UI states — newly created rules will be invisible until prometheus picks them up, and deleted rules linger without a "Deleting" indicator. Confirm the product decision is intentional.
```suggestion
// Document the UX regression in a code comment or linked issue,
// or add a "recently changed" indicator sourced from another signal.
```

:yellow_circle: [hallucination] `prometheusRuleType.grafana.alertingRule` called with possibly-undefined `rule` in `public/app/features/alerting/unified/hooks/useAbilities.ts`:472 (confidence: 65)
At line 472, `prometheusRuleType.grafana.alertingRule(rule)` is invoked unguarded where `rule: GrafanaPromRuleDTO | undefined`. Line 465 explicitly guards `isProvisionedPromRule` with `rule ? ... : false`, proving the author is aware; the inconsistency here likely fails type-checking or throws on `rule.type` deref when `skipToken` is passed.
```suggestion
const isAlertingRule = rule ? prometheusRuleType.grafana.alertingRule(rule) : false;
const isPluginProvided = rule ? isPluginProvidedRule(rule) : false;
```

:yellow_circle: [consistency] `skipToken` is a custom Symbol that shadows RTK Query's well-known `skipToken` in `public/app/features/alerting/unified/hooks/useAbilities.ts`:559 (confidence: 78)
`export const skipToken = Symbol('ability-skip-token');` uses the exact name exported by `@reduxjs/toolkit/query` (used elsewhere in Grafana for skipping RTK Query). Two opaque symbol sentinels with identical names invite auto-import mistakes and silent misbehavior.
```suggestion
export const abilitySkipToken = Symbol('ability-skip-token');
type AbilitySkipToken = typeof abilitySkipToken;
// or import { skipToken } from '@reduxjs/toolkit/query' and reuse it
```

:yellow_circle: [consistency] Removed i18n keys not verified as unused elsewhere in `public/locales/en-US/grafana.json`:1435 (confidence: 80)
`cannot-find-rule-details-for` and `cannot-load-rule-details-for` were used in the deleted `GrafanaRuleLoader.tsx`. No evidence is provided that they are not referenced elsewhere. Removing a live i18n key can cause runtime lookup failures / blank strings in other locales' fallback paths.
```suggestion
// Run: rg "cannot-find-rule-details-for|cannot-load-rule-details-for" -n
// across public/app and public/locales before deleting the keys.
```

:yellow_circle: [testing] OR-combination permission logic never tested with asymmetric inputs in `public/app/features/alerting/unified/components/rule-viewer/AlertRuleMenu.tsx`:79 (confidence: 88)
Every test configures both ability sources identically (both deny or both grant). None of the three distinct OR-logic states (only ruler grants, only grafana-prom grants, neither) are exercised. A `&&`/`||` typo in any of the five abilities would not be caught.
```suggestion
it('shows Delete when only grafana-prom source grants delete', async () => {
  mocks.useRulerRuleAbilities.mockImplementation((_r, _g, a) => a.map(() => [false, false]));
  mocks.useGrafanaPromRuleAbilities.mockImplementation((_r, a) =>
    a.map(act => act === AlertRuleAction.Delete ? [true, true] : [false, false])
  );
  render(<RulesTable rules={[grafanaRule]} />);
  await waitFor(() => expect(screen.getByRole('menuitem', { name: /delete/i })).toBeInTheDocument());
});
```

:yellow_circle: [testing] New `GrafanaRuleListItem` recording-rule and unknown-rule branches untested in `public/app/features/alerting/unified/rule-list/GrafanaRuleListItem.tsx`:1 (confidence: 85)
Only the alerting-rule branch (`AlertRuleListItem`) is exercised via the `GrafanaGroupLoader` integration path. The `RecordingRuleListItem` and `UnknownRuleListItem` branches are never rendered; regressions there would go undetected.
```suggestion
it('renders RecordingRuleListItem for a recording rule', () => {
  const rule = mockGrafanaPromRecordingRule({ uid: 'rec-1', folderUid: 'f1' });
  render(<GrafanaRuleListItem rule={rule} groupIdentifier={groupIdentifier} namespaceName="folder" />);
  expect(screen.getByText(rule.name)).toBeInTheDocument();
});
```

:yellow_circle: [testing] `skipToken` sentinel behavior only exercised via mocks, never the real hook in `public/app/features/alerting/unified/hooks/useAbilities.ts`:559 (confidence: 82)
Because `RulesTable.test.tsx` mocks the whole `useAbilities` module, the `rule === skipToken ? undefined : rule` conditional is never executed under test. A refactor of the sentinel type or conditional would ship undetected.
```suggestion
it('useGrafanaPromRuleAbilities returns [false, false] for all actions when skipToken is passed', () => {
  const { result } = renderHook(() =>
    useGrafanaPromRuleAbilities(skipToken, [AlertRuleAction.Update, AlertRuleAction.Delete])
  );
  result.current.forEach(([supported, allowed]) => {
    expect(supported).toBe(false);
    expect(allowed).toBe(false);
  });
});
```

:yellow_circle: [testing] `getEditableIdentifier` / `getIsProvisioned` helpers and the null-early-return path untested in `public/app/features/alerting/unified/rule-list/components/RuleActionsButtons.V2.tsx`:352 (confidence: 78)
The new helper `getEditableIdentifier` returns `undefined` (triggering a silent `return null`) when neither rule is a Grafana rule. No test exercises this branch, so a regression could cause action buttons to silently disappear for valid rules.
```suggestion
it('renders null when neither rule nor promRule can produce an identifier', () => {
  const cloudPromRule = getCloudRule({ name: 'cloud' });
  const { container } = render(
    <RuleActionsButtons promRule={cloudPromRule} groupIdentifier={cloudGroupIdentifier} />
  );
  expect(container).toBeEmptyDOMElement();
});
```

:yellow_circle: [consistency] Hook naming: plural vs singular convention not documented in `public/app/features/alerting/unified/hooks/useAbilities.ts`:372 (confidence: 72)
PR adds plural variants (`useRulerRuleAbilities`, `useGrafanaPromRuleAbilities`) alongside existing singulars. The convention (singular = one action, plural = array of actions) is reasonable but not documented, risking inconsistent future additions.
```suggestion
/**
 * Naming convention:
 *  - useXAbility(rule, groupIdentifier, action)      — single action
 *  - useXAbilities(rule, groupIdentifier, actions[]) — batch, returns one Ability per action
 */
```

:yellow_circle: [cross-file-impact] `FilterView.tsx` passes `rule` (union-typed?) to strictly typed `GrafanaRuleListItem` in `public/app/features/alerting/unified/rule-list/FilterView.tsx`:154 (confidence: 72)
The old code used `<GrafanaRuleLoader ruleIdentifier={...}>`; the new code passes `<GrafanaRuleListItem rule={rule} ...>` where `GrafanaRuleListItemProps.rule: GrafanaPromRuleDTO` is non-optional. If `rule` in the filter state is a broader union, this is a type error at the call site.
```suggestion
// Narrow rule before passing:
if (isGrafanaPromRule(rule)) {
  return <GrafanaRuleListItem rule={rule} groupIdentifier={gi} namespaceName={ns} />;
}
```

## Nitpicks

:white_circle: [hallucination] Stale `// duplicate` comment left in production code in `public/app/features/alerting/unified/hooks/useAbilities.ts`:455 (confidence: 60)
`const { isEditable, isRemovable, loading } = useIsGrafanaPromRuleEditable(rule); // duplicate` — the comment references a "duplicate" that does not exist in the function body. Likely an AI-generation artifact or leftover TODO marker. Misleading without added information.

:white_circle: [hallucination] `groupIdentifier` namespace import needs verification in `public/app/features/alerting/unified/hooks/useAbilities.ts`:357 (confidence: 55)
`import { getGroupOriginName, groupIdentifier } from '../utils/groupIdentifier';` imports a lowercase value named `groupIdentifier` used at line 396 as `groupIdentifier.fromCombinedRule(rule)`. Pattern matches existing `ruleId` namespace usage, but the `.fromCombinedRule` method must exist on master — verify the namespace export is not fabricated.

:white_circle: [consistency] `.V2` filename suffix deviates from project convention in `public/app/features/alerting/unified/rule-list/components/RuleActionsButtons.V2.tsx`:1 (confidence: 65)
Grafana typically uses lowercase `.v2` or a `v2/` directory; the uppercase `.V2` suffix is non-standard. File comment acknowledges this is a flag-gated copy; reconcile once the feature flag is removed.

:white_circle: [testing] Deleted `matchRules` / transient-state test cases are appropriate cleanup, not coverage loss in `public/app/features/alerting/unified/rule-list/GrafanaGroupLoader.test.tsx`:1 (confidence: 90)
The -64 lines remove tests for the `matchRules` helper and "creating / deleting" state rendering — both of which are correctly removed from production in this PR. This is not a coverage regression on surviving code.

:white_circle: [consistency] Feature-flag-gated duplicate component should carry a removal-tracking link in `public/app/features/alerting/unified/rule-list/components/RuleActionsButtons.V2.tsx`:1 (confidence: 55)
`RuleActionsButtons.V2.tsx` is described as "a copy of RuleActionsButtons.tsx but with the View button removed". Without a tracking issue/TODO, the duplicate is likely to drift.

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: localized to alerting UI module, but touches a permission-gate with backend RBAC as the authoritative boundary | Sensitive Paths: none matched
AI-Authored Likelihood: MEDIUM — several tell-tales (stale `// duplicate` comment, name-collision with RTK Query's `skipToken`, inconsistent null-guarding of `rule` between adjacent lines, signature widening applied to plural but not singular hooks).

Security note: the reviewed security agent (completed) determined that although the OR-logic permits UI actions when either source grants, the authoritative server-side RBAC on the ruler mutation endpoints remains intact. Client-side permission derivation is therefore a UI hint only, not an authorization bypass. No critical security findings.

(0 additional findings below confidence threshold)
