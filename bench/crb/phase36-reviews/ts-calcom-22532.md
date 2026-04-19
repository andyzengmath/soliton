## Summary
17 files changed, ~380 lines added, ~30 lines deleted. 3 findings (1 critical, 2 improvements, 0 nitpicks).
Calendar cache status + delete feature has a silent no-op that defeats its own timestamp-tracking, plus a feature-flag bypass and a missing tRPC cache invalidation.

## Critical

:red_circle: [correctness] `updateManyByCredentialId(id, {})` is a Prisma no-op — `@updatedAt` never bumps in `packages/lib/server/repository/selectedCalendar.ts`:299 (confidence: 92)
`GoogleCalendarService.fetchAvailabilityAndSetCache` calls `SelectedCalendarRepository.updateManyByCredentialId(this.credential.id, {})` intending to refresh `SelectedCalendar.updatedAt`, but Prisma's `updateMany` with `data: {}` emits no SET clause and therefore never triggers `@updatedAt`. The downstream UI that reads `updatedAt` (and the `cacheUpdatedAt` surfaced from `CalendarCache`) will stay stale, silently defeating the feature this PR is built around.
```suggestion
// packages/lib/server/repository/selectedCalendar.ts
static async updateManyByCredentialId(
  credentialId: number,
  data: Prisma.SelectedCalendarUpdateManyMutationInput
) {
  return await prisma.selectedCalendar.updateMany({
    where: { credentialId },
    data: { ...data, updatedAt: new Date() },
  });
}

// packages/app-store/googlecalendar/lib/CalendarService.ts
await SelectedCalendarRepository.updateManyByCredentialId(this.credential.id, {
  updatedAt: new Date(),
});
```
<details><summary>More context</summary>

Two issues compound here:

1. **Empty-data no-op.** Prisma only applies `@updatedAt` when it actually emits an UPDATE with ≥ 1 SET column. `updateMany({ where, data: {} })` short-circuits to zero affected rows (or errors, depending on Prisma version). The comment on the call site — `// Update SelectedCalendar.updatedAt for all calendars under this credential` — describes an intent that the code does not achieve.
2. **Wrong parameter type.** The signature declares `data: Prisma.SelectedCalendarUpdateInput`, but `updateMany` requires `Prisma.SelectedCalendarUpdateManyMutationInput` (scalar-only, no nested relation writes). It compiles today only because `{}` satisfies both shapes; any future caller passing a non-trivial payload will hit a strict-mode type error.

Explicitly writing `updatedAt: new Date()` forces both a real UPDATE and the correct intent, independent of Prisma's `@updatedAt` timing.
</details>

## Improvements

:yellow_circle: [correctness] `connectedCalendars.handler.ts` instantiates `CalendarCacheRepository` directly, bypassing the feature-flag mock in `packages/trpc/server/routers/viewer/calendars/connectedCalendars.handler.ts`:27 (confidence: 88)
Every other consumer of `ICalendarCacheRepository` goes through the factory that returns `CalendarCacheRepositoryMock` when calendar-cache is disabled (its `getCacheStatusByCredentialIds` logs "Skipping … due to calendar-cache being disabled" and returns `[]`). This handler hard-codes `new CalendarCacheRepository()`, so the flag is silently ignored and every call will hit the real `calendarCache.groupBy` query regardless of plan/flag state.
```suggestion
import { CalendarCache } from "@calcom/features/calendar-cache/calendar-cache";
// ...
const cacheRepository = await CalendarCache.initFromCredentialOrUser(/* or the existing factory used elsewhere */);
const cacheStatuses = await cacheRepository.getCacheStatusByCredentialIds(credentialIds);
```
<details><summary>More context</summary>

The mock exists specifically so that the repository interface is the seam for the feature flag. Direct instantiation here is the only place in the diff that skips that seam, which makes this handler inconsistent with how `deleteManyByCredential` and the rest of the calendar-cache subsystem are gated. On installations that have calendar-cache turned off (self-hosted, certain plans), this introduces a new DB hit per connectedCalendars query for users who should see no cache UI at all.
</details>

:yellow_circle: [correctness] `deleteCacheMutation` never invalidates `viewer.calendars.connectedCalendars` — stale `cacheUpdatedAt` after delete in `packages/features/apps/components/CredentialActionsDropdown.tsx`:31 (confidence: 85)
`deleteCacheMutation.onSuccess` only calls the optional parent `onSuccess?.()`, while the sibling `disconnectMutation` in the same file correctly invalidates `viewer.calendars.connectedCalendars` and `viewer.apps.integrations` in `onSettled`. After a successful cache delete the UI keeps showing the old `cacheUpdatedAt` timestamp (and the "Cache Status" row) until the page is refreshed, breaking the visible feedback loop the feature is supposed to provide.
```suggestion
const deleteCacheMutation = trpc.viewer.calendars.deleteCache.useMutation({
  onSuccess: () => {
    showToast(t("cache_deleted_successfully"), "success");
    onSuccess?.();
  },
  onError: () => {
    showToast(t("error_deleting_cache"), "error");
  },
  async onSettled() {
    await utils.viewer.calendars.connectedCalendars.invalidate();
  },
});
```
<details><summary>More context</summary>

`utils` is already created in the component (used by `disconnectMutation`), so adding the invalidation is a one-line change. Relying on the parent `onChanged` callback is fragile: the wrapper this component is rendered from may not always refetch, and the tRPC cache key for connectedCalendars is the authoritative source of truth for `cacheUpdatedAt`. This asymmetry between the two mutations on the same component also invites regression — future maintainers will reasonably assume both mutations refresh the same data.
</details>

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: repository-interface widening (`ICalendarCacheRepository`, `UserWithCalendars`), new TRPC mutation, non-trivial Prisma migration on `CalendarCache` | Sensitive Paths: `packages/prisma/migrations/`, `trpc/server/routers/viewer/calendars/` (authed mutation)
AI-Authored Likelihood: HIGH (head branch `devin/calendar-cache-tooltip-1752595047`)

(10 additional findings below confidence threshold 85 — notably: `deleteCache` handler uses plain `throw new Error` instead of `TRPCError` and does not cover team-owned credentials; `scripts/test-gcal-webhooks.sh` has sed/`/tmp` injection and BSD-vs-GNU `sed -i ''` portability bugs; `z.number()` on `credentialId` should be `z.number().int().positive()`; `Intl.DateTimeFormat("en-US", …)` hard-codes locale; `i18next` `interpolation: { escapeValue: false }` sets a risky precedent on a translation key; `UserWithCalendars` Pick widened with `updatedAt`/`googleChannelId` — any caller outside `UserRepository` that constructs this shape will fail to type-check; migration SQL comment is stale and misleading.)
