## Summary
17 files changed, 378 lines added, 31 lines deleted. 9 findings (2 critical, 7 improvements, 0 nitpicks).
New calendar cache status + deleteCache flow ships with two likely TypeScript compile breaks on the shared `UserWithCalendars` type, a handful of correctness slips in the new React component and Google-calendar write path, and zero tests for two destructive new APIs.

## Critical
:red_circle: [cross-file-impact] `Pick<SelectedCalendar, "googleChannelId">` references a field not present on the Prisma model in packages/lib/getConnectedDestinationCalendars.ts:271 (confidence: 90)
`UserWithCalendars.allSelectedCalendars` / `userLevelSelectedCalendars` now pick `"updatedAt" | "googleChannelId"` from `SelectedCalendar`. The diff adds a migration only for `CalendarCache.updatedAt` — there is no schema or migration change adding `googleChannelId` to `SelectedCalendar`. Unless that field already exists in the deployed schema (not demonstrated in this PR), every file importing `UserWithCalendars` (the two repositories updated here plus any other consumer of `getConnectedDestinationCalendars`) will fail to typecheck. `user.ts` also adds `googleChannelId: true` to the select, which would throw at runtime if the field is truly absent.
```suggestion
// Either confirm SelectedCalendar.googleChannelId already exists on the schema,
// or add a migration + schema field before merging. If the field is only needed
// from CalendarCache, drop it from the Pick<>:
allSelectedCalendars: Pick<SelectedCalendar, "externalId" | "integration" | "eventTypeId" | "updatedAt">[];
```

:red_circle: [cross-file-impact] `delegationCredentialId` prop coerced from `undefined` to `null` without updating the consumer in packages/platform/atoms/selected-calendars/wrappers/SelectedCalendarsSettingsWebWrapper.tsx:369 (confidence: 85)
The prior callsite passed `connectedCalendar.delegationCredentialId` directly (a `string | undefined`). The PR now passes `connectedCalendar.delegationCredentialId || null`, handing `string | null` to `CalendarSwitch` whose prop type was not updated in this diff. If `CalendarSwitch.delegationCredentialId` is typed `string | undefined` (the prior contract), this is a compile error; if it is `string`, passing `null` is a runtime-type break that TS may or may not catch depending on strictness.
```suggestion
delegationCredentialId={connectedCalendar.delegationCredentialId ?? undefined}
```

## Improvements
:yellow_circle: [correctness] i18next `interpolation` option misplaced inside values object in packages/features/apps/components/CredentialActionsDropdown.tsx:138 (confidence: 95)
`t("cache_last_updated", { timestamp: ..., interpolation: { escapeValue: false } })` passes `interpolation` as a translation variable, not as an i18next runtime option. i18next's second argument is the values bag; per-call overrides for `escapeValue` must go in a separate third argument (and only work when the option is supported at call-time). As written, `{{interpolation}}` would be substituted in the translation string if referenced, and `escapeValue: false` is silently ignored. The pre-formatted `Intl.DateTimeFormat` string contains no HTML, so the intended behavior is already safe — but the `interpolation` key is dead code that suggests the author misunderstood the API.
```suggestion
{t("cache_last_updated", {
  timestamp: new Intl.DateTimeFormat("en-US", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(new Date(cacheUpdatedAt)),
})}
```

:yellow_circle: [correctness] `hasCache` guard permits invalid `Date` values that crash `Intl.DateTimeFormat.format` in packages/features/apps/components/CredentialActionsDropdown.tsx:119 (confidence: 88)
`hasCache = isGoogleCalendar && cacheUpdatedAt` is a truthy check on a `Date` object — any `Date` (including `new Date("")` / `Invalid Date`) is truthy. More importantly the prop is typed `Date | null` but line 142 wraps it again with `new Date(cacheUpdatedAt)`; if serialization ever delivers the value as an ISO string, the double-wrap happens to work, but if the value arrives as `undefined` (e.g. Atoms-SDK response before type update — see critical #1), `new Date(undefined)` yields an `Invalid Date` and `Intl.DateTimeFormat.format(...)` throws `RangeError`, crashing the entire connected-calendars page.
```suggestion
const hasCache =
  isGoogleCalendar && cacheUpdatedAt != null && !isNaN(new Date(cacheUpdatedAt).getTime());
// and in the JSX, pass the Date directly:
.format(new Date(cacheUpdatedAt))
```

:yellow_circle: [correctness] `updateManyByCredentialId(credentialId, {})` is a Prisma no-op and never bumps `updatedAt` in packages/app-store/googlecalendar/lib/CalendarService.ts:1022 (confidence: 85)
The inline comment reads "Update SelectedCalendar.updatedAt for all calendars under this credential," but Prisma's `updateMany` emits no SQL SET clause when `data` is empty — no rows are touched and `@updatedAt` is not triggered. Because the UI now relies on `cacheUpdatedAt` from `CalendarCache` (not `SelectedCalendar.updatedAt`), this call is currently inert. Either remove it (the PR description itself questions the mirror) or pass a concrete field write so the timestamp actually updates.
```suggestion
await SelectedCalendarRepository.updateManyByCredentialId(this.credential.id, {
  updatedAt: new Date(),
});
// Or delete the call outright — the cache timestamp already lives on CalendarCache.
```

:yellow_circle: [testing] New destructive `deleteCacheHandler` has zero test coverage in packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:1 (confidence: 98)
The handler bulk-deletes all `CalendarCache` rows for a credential after a single `findFirst({id, userId})` authorization check. That predicate is the sole defense against cross-user cache deletion; no test exists that would catch a regression refactoring away the `userId` binding, or that exercises the not-found / wrong-user / no-cache-rows paths. This is a high-leverage file to leave untested.
```suggestion
// packages/trpc/server/routers/viewer/calendars/deleteCache.handler.test.ts
import { deleteCacheHandler } from "./deleteCache.handler";
import { prismaMock } from "@calcom/testing/prismaMock";

it("rejects cross-user credential", async () => {
  prismaMock.credential.findFirst.mockResolvedValue(null);
  await expect(
    deleteCacheHandler({ ctx: { user: { id: 99 } }, input: { credentialId: 1 } }),
  ).rejects.toThrow(/not found or access denied/);
  expect(prismaMock.calendarCache.deleteMany).not.toHaveBeenCalled();
});

it("deletes cache for authorized caller", async () => {
  prismaMock.credential.findFirst.mockResolvedValue({ id: 1, userId: 42 } as never);
  prismaMock.calendarCache.deleteMany.mockResolvedValue({ count: 3 } as never);
  await expect(
    deleteCacheHandler({ ctx: { user: { id: 42 } }, input: { credentialId: 1 } }),
  ).resolves.toEqual({ success: true });
});
```

:yellow_circle: [testing] `getCacheStatusByCredentialIds` has no tests for empty input or missing-row edge cases in packages/features/calendar-cache/calendar-cache.repository.ts:243 (confidence: 95)
The new `groupBy` + `_max(updatedAt)` query omits credentials that have no cache rows (callers receive a shorter array than they passed in). `connectedCalendarsHandler` then builds a `Map` from the result and calls `.get(credentialId) || null` — that codepath works today but is one refactor away from silently dropping credentials. Also unit-test the empty-`credentialIds` path, which on PostgreSQL can surface an `IN ()` syntax error depending on Prisma version.
```suggestion
// packages/features/calendar-cache/calendar-cache.repository.test.ts
it("returns [] for empty input without hitting the DB", async () => {
  expect(await repo.getCacheStatusByCredentialIds([])).toEqual([]);
});

it("omits credentials that have no cache rows", async () => {
  prismaMock.calendarCache.groupBy.mockResolvedValue([
    { credentialId: 1, _max: { updatedAt: new Date("2024-01-01") } } as never,
  ]);
  const result = await repo.getCacheStatusByCredentialIds([1, 2]);
  expect(result.map((r) => r.credentialId)).toEqual([1]);
});
```

:yellow_circle: [testing] `CredentialActionsDropdown` conditional render branches are untested in packages/features/apps/components/CredentialActionsDropdown.tsx:117 (confidence: 88)
The component has three independent gates (`canDisconnect`, `hasCache`, `isGoogleCalendar`) combining into four meaningful render states, including a silent `return null` when both gates are false. Future refactors to the gate expressions will pass type-check while making the menu disappear. A small RTL test per state prevents that.
```suggestion
// Covers: null-return for disabled delegation + no cache, cache-only for delegation + cache,
// disconnect-only for non-google cred, both sections for google + cache.
it("renders nothing when delegation is set and no cache", () => {
  const { container } = render(
    <CredentialActionsDropdown
      credentialId={1}
      integrationType={GOOGLE_CALENDAR_TYPE}
      delegationCredentialId="del-1"
      cacheUpdatedAt={null}
    />,
  );
  expect(container.firstChild).toBeNull();
});
```

:yellow_circle: [consistency] Hardcoded Tailwind colors instead of cal.com semantic tokens in packages/features/apps/components/CredentialActionsDropdown.tsx:136 (confidence: 85)
`text-gray-900 dark:text-white` / `text-gray-500 dark:text-white` bypass cal.com's semantic color tokens (`text-emphasis`, `text-default`, `text-subtle`) used elsewhere in the apps components. Additionally, `text-gray-500 dark:text-white` flips from a subtle gray in light mode to the high-emphasis color in dark mode, which is inconsistent with the rest of the drop-down's hierarchy.
```suggestion
<div className="text-emphasis text-sm font-medium">{t("cache_status")}</div>
<div className="text-subtle text-xs">
  {t("cache_last_updated", {
    timestamp: new Intl.DateTimeFormat("en-US", {
      dateStyle: "short",
      timeStyle: "short",
    }).format(new Date(cacheUpdatedAt)),
  })}
</div>
```

## Risk Metadata
Risk Score: 74/100 (HIGH) | Blast Radius: 17 files across 7 packages (schema.prisma, type contracts on UserWithCalendars, core connectedCalendars tRPC handler) | Sensitive Paths: *migration* (packages/prisma/migrations/…), *credential* (CredentialActionsDropdown.tsx, deleteCache.handler.ts)
AI-Authored Likelihood: HIGH (branch prefix `devin/…`; uniform 157/33/79-line boilerplate across three new files)

(14 additional findings below confidence threshold 85 — lower-confidence items included deleteCache rate limiting (65), `sed -i ''` BSD syntax breaking Linux (82), `cleanup; exit 0` swallowing rate-limit errors (80), empty-`IN ()` behavior in Prisma groupBy (72), suspect `GOOGLE_CALENDAR_TYPE` import path (70), possibly non-existent `Dropdown` root export (65), unverified `trpc.viewer.credentials.delete` route (60), redundant `@default(now()) @updatedAt` (55), unsanitized `sed` delimiter in webhook script (60), Atoms-SDK `cacheUpdatedAt` type definition not updated (75), bare `Error` vs `TRPCError` (80), emoji in bash status output (75), redundant blank line in wrapper (65), i18n interpolation as hallucinated option key (70 — deduped into correctness 95 above))
