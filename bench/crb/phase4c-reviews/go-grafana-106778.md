## Summary
14 files changed, 542 lines added, 418 lines deleted. 5 findings (0 critical, 5 improvements, 0 nitpicks).
Alert-list view is migrated off the Ruler API to rely solely on the Prometheus rules API; the patch is largely mechanical, but the new dual-source ability hooks merge permissions with an OR that can widen access, the UX loses "Creating/Deleting" affordances, and the `isProvisionedPromRule` gate depends on a new `provenance` field whose backend plumbing is not visible in this diff.

## Improvements
:yellow_circle: [correctness] Permission OR in `AlertRuleMenu` / `RuleActionsButtons.V2` can surface actions the ruler explicitly denies in `public/app/features/alerting/unified/components/rule-viewer/AlertRuleMenu.tsx`:66 (confidence: 82)
`canPause`, `canDelete`, `canDuplicate`, `canSilence`, `canExport`, and `canEditRule` are now computed as `(rulerSupported && rulerAllowed) || (grafanaSupported && grafanaAllowed)`. When both hooks resolve (detail view, where a `rulerRule` is still passed alongside a matching `promRule`) the more permissive branch wins. Ruler-backed permission checks go through `getRulesPermissions(rulesSourceName)` and `useIsRuleEditable`, while the Grafana-prom branch uses folder-scoped `hasPermissionInMetadata` via `useIsGrafanaPromRuleEditable`. If those two sources disagree (e.g. ruler says "not allowed" because `isRulerAvailable === false`, but folder metadata still reports edit rights) the OR falls through to the permissive answer and the menu item is rendered. Prefer AND-ing within a single source, or pick one authoritative source per callsite based on whether the caller is the list view (promRule) or the detail view (rulerRule).
```suggestion
  // pick the authoritative source per callsite; don't OR permissive results across sources
  const isFromListView = !rulerRule;
  const [pauseSupported, pauseAllowed] = isFromListView ? grafanaPauseAbility : rulerPauseAbility;
  const canPause = pauseSupported && pauseAllowed;
  // …repeat for delete / duplicate / silence / export
```

:yellow_circle: [correctness] `useAllGrafanaPromRuleAbilities` uses `AlwaysSupported` instead of gating on ruler availability in `public/app/features/alerting/unified/hooks/useAbilities.ts`:303 (confidence: 68)
`useAllRulerRuleAbilities` returns `MaybeSupported = loading ? NotSupported : isRulerAvailable`, so edit/delete/pause are disabled whenever the Ruler data source is not reachable. The new `useAllGrafanaPromRuleAbilities` drops this gate: `MaybeSupported = loading ? NotSupported : AlwaysSupported`. For Grafana-managed rules the Ruler is in-process and the "always supported" assumption is usually correct, but users with the legacy "Ruler not available" error states (e.g. during a Grafana outage or read-only mode) will now see enabled edit/pause/delete buttons that fail on click. Consider still consulting `useIsRuleEditable('grafana', undefined)` (or an equivalent ruler-availability probe) so the UI stays consistent with the rest of the codebase's "maybe supported" semantics.
```suggestion
  const { isRulerAvailable = false } = useIsRuleEditable('grafana');
  const MaybeSupported = loading ? NotSupported : isRulerAvailable;
```

:yellow_circle: [cross-file-impact] `isProvisionedPromRule` depends on a `provenance` field that this PR only types, not populates in `public/app/features/alerting/unified/utils/rules.ts`:171 (confidence: 78)
The new `isProvisionedPromRule` reads `promRule.provenance`, and the frontend type was extended with `provenance?: string` in `public/app/types/unified-alerting-dto.ts`, but no backend change is present in this diff. If the Grafana Prometheus-rules endpoint is not already serializing `provenance` on each rule, `isProvisionedPromRule` will return `false` for provisioned rules, `immutableRule` stays `false` in `useAllGrafanaPromRuleAbilities`, and provisioned rules will expose Edit/Delete/Pause in the list view — a regression relative to the old ruler-based path. Confirm the backend already emits this field (and that it covers recording rules, not just alerting rules) before this merges, or gate the migration behind a feature flag.
```suggestion
// verify the API response includes `provenance` in an integration test before relying on it
expect(response.data.groups[0].rules[0]).toHaveProperty('provenance');
```

:yellow_circle: [consistency] Removing the "Creating" / "Deleting" transient UI states is a user-visible change with no mitigation in `public/app/features/alerting/unified/rule-list/GrafanaGroupLoader.tsx`:65 (confidence: 66)
The previous implementation rendered a `RuleOperation.Creating` row for rules present in the Ruler but not yet in Prometheus, and a `RuleOperation.Deleting` row for the reverse case, to paper over the Ruler→Prometheus propagation delay. This patch deletes both paths (alongside `matchRules` and the entire `GrafanaRuleLoader.tsx`), so a freshly-created rule will silently disappear from the list for a few seconds and a deleted rule will appear to linger until the next poll. The PR description frames the change as "remove ruler from the alert list view" and does not acknowledge this UX regression. Worth either (a) documenting the propagation delay in a release note, (b) keeping a short client-side optimistic "Creating"/"Deleting" overlay keyed on a local mutation cache, or (c) confirming that the new Grafana Prometheus backend is now strongly-consistent on create/delete so the gap is imperceptible.
```suggestion
// If propagation is not strongly consistent, reintroduce the optimistic overlay:
// keep a short-lived local cache of pending create/delete ops keyed by rule UID
// and render RuleOperation.Creating / RuleOperation.Deleting until the next poll
// returns a state that matches the local op.
```

:yellow_circle: [consistency] Leftover `// duplicate` comment and unused `MaybeSupportedUnlessImmutable` branch labeling are confusing in `public/app/features/alerting/unified/hooks/useAbilities.ts`:298 (confidence: 88)
`const { isEditable, isRemovable, loading } = useIsGrafanaPromRuleEditable(rule); // duplicate` has a trailing `// duplicate` comment that reads as a TODO marker from development. It's unclear whether it means "this is duplicated from `useAllRulerRuleAbilities`, consider extracting" or "this line is redundant, delete it". Either intent should be resolved before merge — leaving the bare word in the shipped code will confuse future readers and trip linters that forbid TODO-like comments without tracking.
```suggestion
  const { isEditable, isRemovable, loading } = useIsGrafanaPromRuleEditable(rule);
```

## Risk Metadata
Risk Score: 38/100 (LOW-MEDIUM) | Blast Radius: alerting unified list view + ability hooks (14 files, all `public/app/features/alerting/unified/**`); removes two i18n keys (`cannot-find-rule-details-for`, `cannot-load-rule-details-for`) | Sensitive Paths: none
AI-Authored Likelihood: LOW
