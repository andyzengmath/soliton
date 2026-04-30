## Summary
14 files changed, 542 lines added, 418 lines deleted. 4 findings (1 critical, 3 improvements, 0 nitpicks).
Solid refactor that drops the ruler API from the alert list view, but the `useAllAlertRuleAbilities` rewrite silently disables federated-rule immutability and Grafana-managed recording rules lose four list-view actions.

## Critical
:red_circle: [correctness] Federated Mimir rule groups become editable/deletable in `public/app/features/alerting/unified/hooks/useAbilities.ts`:226 (confidence: 92)
The deprecated `useAllAlertRuleAbilities` previously computed `const isFederated = isFederatedRuleGroup(rule.group);` and used it in `immutableRule = isProvisioned || isFederated || isPluginProvided` to block edits/deletes on federated rule groups. The refactor delegates to `useAllRulerRuleAbilities`, which hardcodes `const isFederated = false;` with a TODO. Federated rule groups are a real Mimir feature (cross-tenant federated rules), and any Grafana instance connected to such a Mimir backend will now expose Edit/Delete/Pause/DeletePermanently UI on rules that the ruler API cannot mutate. The `import { isFederatedRuleGroup, ... } from '../utils/rules';` was removed from the file, so this is a deliberate but unfinished change. Either restore the check or surface federated state on `RuleGroupIdentifierV2` so `useAllRulerRuleAbilities` can consult it.
```suggestion
// In useAllRulerRuleAbilities, accept and honor the federated flag:
export function useAllRulerRuleAbilities(
  rule: RulerRuleDTO | undefined,
  groupIdentifier: RuleGroupIdentifierV2,
  isFederated = false,
): Abilities<AlertRuleAction> { ... }

// In useAllAlertRuleAbilities (CombinedRule caller):
const isFederated = isFederatedRuleGroup(rule.group);
return useAllRulerRuleAbilities(rule.rulerRule, groupIdentifierV2, isFederated);
```

## Improvements
:yellow_circle: [correctness] Grafana-managed recording rules lose Pause / Restore / ModifyExport / DeletePermanently in `public/app/features/alerting/unified/hooks/useAbilities.ts`:332 (confidence: 88)
`useAllGrafanaPromRuleAbilities` gates Pause, Restore, ModifyExport, and DeletePermanently on `isAlertingRule = prometheusRuleType.grafana.alertingRule(rule)`, which is `false` for recording rules. The previous `useAllRulerRuleAbilities` gated the same actions on `isGrafanaManagedAlertRule = rulerRuleType.grafana.rule(rule)`, which is `true` for both alerting and recording Grafana-managed rules. Because `GrafanaRuleListItem` now flows the prom-only path with no ruler rule, recording rules in the list view will silently miss those four actions even when the user has full permissions. The Silence gating on `isAlertingRule` is correct (recording rules have no instances to silence), but persistence operations should not require alerting-rule type.
```suggestion
const isGrafanaManagedRule = true; // every GrafanaPromRuleDTO is Grafana-managed
const isAlertingRule = prometheusRuleType.grafana.alertingRule(rule);

[AlertRuleAction.Silence]: [silenceSupported, canSilenceInFolder && isAlertingRule],
[AlertRuleAction.ModifyExport]: [isGrafanaManagedRule, exportAllowed],
[AlertRuleAction.Pause]: [MaybeSupportedUnlessImmutable && isGrafanaManagedRule, isEditable ?? false],
[AlertRuleAction.Restore]: [MaybeSupportedUnlessImmutable && isGrafanaManagedRule, isEditable ?? false],
[AlertRuleAction.DeletePermanently]: [
  MaybeSupportedUnlessImmutable && isGrafanaManagedRule,
  (isRemovable && isAdmin()) ?? false,
],
```

:yellow_circle: [correctness] Loss of `returnTo` in rule-detail href removes back-navigation from list view in `public/app/features/alerting/unified/rule-list/GrafanaRuleListItem.tsx`:30 (confidence: 85)
The deleted `GrafanaRuleLoader.tsx` built the link as `createRelativeUrl(\`/alerting/grafana/${uid}/view\`, { returnTo })` where `returnTo = createReturnTo()`. The new `GrafanaRuleListItem.tsx` calls `createRelativeUrl(\`/alerting/grafana/${uid}/view\`)` with no `returnTo`. Users navigating from the list view to the rule detail page no longer get the "Back to list" link wired up; the rule detail page falls back to its default landing page. This is a quiet UX regression specifically introduced by this PR's component swap and is easy to restore.
```suggestion
import { createReturnTo } from '../hooks/useReturnTo';

export function GrafanaRuleListItem({ rule, groupIdentifier, namespaceName, operation, showLocation = true }: GrafanaRuleListItemProps) {
  const { name, uid, labels, provenance } = rule;
  const returnTo = createReturnTo();

  const commonProps: RuleListItemCommonProps = {
    // ...
    href: createRelativeUrl(`/alerting/grafana/${uid}/view`, { returnTo }),
    // ...
  };
}
```

:yellow_circle: [correctness] `RuleActionsButtons` returns null for Cloud-promRule-only callers, breaking parent layout assumptions in `public/app/features/alerting/unified/rule-list/components/RuleActionsButtons.V2.tsx`:60 (confidence: 78)
`RequireAtLeastOne<{ rule?: RulerRuleDTO; promRule?: Rule }>` allows callers to pass a Cloud (Mimir/Loki) `promRule` with no `rulerRule`. `getEditableIdentifier` then falls through to `logWarning('Unable to construct an editable rule identifier')` and returns `undefined`, after which the component returns `null`. Parents that conditionally render flexbox spacing, dividers, or aria-labelled wrappers around `<RuleActionsButtons />` will see layout shifts when the component disappears entirely instead of rendering an empty action set. Returning an empty `<Stack>` (or surfacing the warning to a parent error boundary) keeps layout invariant and makes the silent failure observable.
```suggestion
if (!identifier) {
  return <Stack direction="row" gap={1} />;
}
```

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: 14 files across `public/app/features/alerting/unified/{hooks,rule-list,components}` plus `public/app/types/unified-alerting-dto.ts` and locales | Sensitive Paths: none (frontend permission-UI only — server-side RBAC remains the source of truth)
AI-Authored Likelihood: MEDIUM (the `// duplicate` artifact at `useAbilities.ts:454`, the TODO-only `isFederated = false` carve-out, and the boilerplate-heavy ability OR-chains in `AlertRuleMenu.tsx` are consistent with assistant-generated code)

(1 additional finding below confidence threshold)
