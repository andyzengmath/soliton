## Summary
14 files changed, 542 lines added, 418 lines deleted. 3 findings (1 critical, 2 improvements).
Ruler API calls are removed from the alert list view in favor of new `GrafanaPromRuleDTO`-based ability hooks; the refactor introduces a permissions regression for recording rules and a pause-state staleness window.

## Critical
:red_circle: [correctness] Recording rules lose DeletePermanently / ModifyExport in `public/app/features/alerting/unified/hooks/useAbilities.ts`:320 (confidence: 92)
The pre-PR `useAllRulerRuleAbilities` gated `DeletePermanently`, `Pause`, `Restore`, and `ModifyExport` on `isGrafanaManagedAlertRule = rulerRuleType.grafana.rule(rule)`, which is `true` for BOTH Grafana alerting and Grafana recording rules. The new `useAllGrafanaPromRuleAbilities` gates all four on `isAlertingRule = prometheusRuleType.grafana.alertingRule(rule)`, which is `false` for recording rules. While narrowing `Pause` / `Restore` / `Silence` to alerting-only is semantically reasonable, `DeletePermanently` is an administrative lifecycle action that previously applied to any Grafana-managed rule, and `ModifyExport` (the Grafana export endpoint accepts any Grafana rule UID regardless of type) was also available for recording rules. After this change, recording rules shown in the list view cannot be permanently deleted or exported through their action menu — behavior that worked before.
```suggestion
const isGrafanaRule = rule !== undefined; // All GrafanaPromRuleDTO are Grafana-managed
const isAlertingRule = prometheusRuleType.grafana.alertingRule(rule);

[AlertRuleAction.ModifyExport]:      [isGrafanaRule, exportAllowed],
[AlertRuleAction.DeletePermanently]: [
  MaybeSupportedUnlessImmutable && isGrafanaRule,
  (isRemovable && isAdmin()) ?? false,
],
// Keep these alerting-only (unchanged):
[AlertRuleAction.Silence]: [silenceSupported, canSilenceInFolder && isAlertingRule],
[AlertRuleAction.Pause]:   [MaybeSupportedUnlessImmutable && isAlertingRule, isEditable ?? false],
[AlertRuleAction.Restore]: [MaybeSupportedUnlessImmutable && isAlertingRule, isEditable ?? false],
```

## Improvements
:yellow_circle: [correctness] `logWarning('Unable to construct an editable rule identifier')` fires during render in `public/app/features/alerting/unified/rule-list/components/RuleActionsButtons.V2.tsx`:147 (confidence: 88)
`getEditableIdentifier` is invoked synchronously on every render of `RuleActionsButtons`. When both `rule` and a valid Grafana `promRule` are absent — for example, a transient loading state or a non-Grafana rule accidentally routed to this component — `logWarning` fires on every render. In React StrictMode this doubles, and because `RuleActionsButtons` renders once per row in a list that commonly contains 80+ rules, a single misconfigured render pass can emit dozens of warnings. The current call sites look safe, but because the props type now accepts `rule?` and `promRule?` independently, the footgun will bite future callers.
```suggestion
const identifier = useMemo(() => {
  const id = getEditableIdentifier(groupIdentifier, rule, promRule);
  if (!id) {
    // called inside useMemo → once per (rule, promRule) change, not per render
    logWarning('Unable to construct an editable rule identifier');
  }
  return id;
}, [groupIdentifier, rule, promRule]);
```

:yellow_circle: [correctness] `isPaused` loses the ruler-authoritative fallback in `public/app/features/alerting/unified/rule-list/GrafanaRuleListItem.tsx`:37 (confidence: 85)
The deleted `GrafanaRuleLoader.tsx` computed `isPaused: rule?.isPaused ?? is_paused`, falling back to `rulerRule.grafana_alert.is_paused` — the authoritative value immediately after a pause/unpause operation. The new component uses `isPaused: rule?.isPaused`, where `rule` is the Prometheus API response polled at `RULE_LIST_POLL_INTERVAL_MS`. Immediately after a user pauses a rule, the Prometheus response remains stale for up to one polling interval, so the pause badge will show the wrong state for several seconds. Users who pause a rule and look at the list right away will see an incorrect indicator.
```suggestion
// If an optimistic cache update is available on the pause mutation, upsert
// the prom cache entry there so isPaused is immediately correct. Otherwise
// document the known staleness window.
isPaused: rule?.isPaused,
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: 14 files across alerting/unified (hooks, rule-list, components, types); touches permission gating logic used throughout the alert list and rule viewer | Sensitive Paths: none matched
AI-Authored Likelihood: LOW

(5 additional findings below confidence threshold — 2 type-soundness issues on `useRulerRuleAbility(undefined, ...)` and `GrafanaPromRuleDTO` passed where `Rule` is expected, 1 stray `// duplicate` comment at `useAbilities.ts`, 1 deprecated-caller migration concern, 1 `AlertRuleMenu` prop-type mismatch duplicate.)
