## Summary
17 files changed, 379 lines added, 31 lines deleted. 6 findings (3 critical, 3 improvements).
Two critical correctness bugs block merge: a no-op `updateMany({}, {})` that targets the wrong table, and a missing query invalidation after `deleteCache` that leaves a permanently stale cache timestamp in the UI. Authorization test coverage for the new mutation is absent.

## Critical

:red_circle: [correctness] `updateManyByCredentialId(this.credential.id, {})` is a no-op AND targets the wrong table in `packages/app-store/googlecalendar/lib/CalendarService.ts`:1021 (confidence: 97)
The call passes an empty `data: {}` object. Prisma's `@updatedAt` is client-side injection — Prisma Client only injects the auto-timestamp when at least one field is being written, so `updateMany({ where, data: {} })` either no-ops (leaves `updatedAt` unchanged) or raises a validation error depending on the client version. The in-line comment says "Update SelectedCalendar.updatedAt for all calendars under this credential," documenting an intent that cannot be achieved this way. More importantly, even if the call did work it would update `SelectedCalendar.updatedAt`, but the UI reads `cacheUpdatedAt` via `getCacheStatusByCredentialIds` which queries `CalendarCache.updatedAt` — a completely different table. The "Cache Status / Last updated" tooltip introduced by this PR therefore cannot be driven by this line. The line on its own is harmless (no-op) but it misleads future readers about how the cache timestamp propagates.
```suggestion
      await this.setAvailabilityInCache(parsedArgs, data);
    }
  }
```
The `CalendarCache.updatedAt` column is already declared with `@updatedAt` in `schema.prisma`, so `setAvailabilityInCache` already bumps it on every upsert. Remove the dead call. If a manual bump is ever needed, target the correct table: `prisma.calendarCache.updateMany({ where: { credentialId: this.credential.id }, data: { updatedAt: new Date() } })`.

:red_circle: [correctness] `deleteCacheMutation` never invalidates `connectedCalendars` — UI shows stale `cacheUpdatedAt` after successful deletion in `packages/features/apps/components/CredentialActionsDropdown.tsx`:92 (confidence: 95)
After a successful delete, all `CalendarCache` rows for the credential are gone, so the server-computed `cacheUpdatedAt` should become `null`. But `deleteCacheMutation` only fires a toast and calls `onSuccess?.()` — it never invalidates the `trpc.viewer.calendars.connectedCalendars` query. The sibling `disconnectMutation` four lines below (see the `async onSettled()` block that calls `utils.viewer.calendars.connectedCalendars.invalidate()` and `utils.viewer.apps.integrations.invalidate()`) follows the correct pattern. The result is that after "Delete cached data" succeeds, the dropdown still renders `hasCache = true` and the "Last updated: …" label keeps showing the deleted timestamp until the user does a hard page refresh. The bug is also symmetric with the one above: together they mean the cache timestamp in the UI essentially only ever changes on full page reload.
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

:red_circle: [testing] No automated test covers the authorization boundary of the new `deleteCache` mutation in `packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts`:1 (confidence: 97)
`deleteCacheHandler` is an authenticated write mutation that takes a caller-supplied `credentialId` and deletes rows from `CalendarCache`. Its only protection is the `findFirst({ where: { id: credentialId, userId: user.id } })` ownership check. No test file was added in this PR, so there is no guard against a future refactor silently weakening this check (e.g. dropping the `userId` clause, switching to `findUnique`, moving the check above the zod validation, etc.). Authorization regressions on write paths are exactly the class of defect that unit tests cheaply prevent.
```suggestion
// packages/trpc/server/routers/viewer/calendars/deleteCache.handler.test.ts
describe("deleteCacheHandler", () => {
  it("rejects when the credentialId belongs to another user", async () => {
    const ctx = { user: { id: 1 } } as any;
    prismaMock.credential.findFirst.mockResolvedValue(null);
    await expect(deleteCacheHandler({ ctx, input: { credentialId: 99 } }))
      .rejects.toThrow(/not found|access denied/i);
    expect(prismaMock.calendarCache.deleteMany).not.toHaveBeenCalled();
  });

  it("deletes cache rows when the credential belongs to the caller", async () => {
    const ctx = { user: { id: 1 } } as any;
    prismaMock.credential.findFirst.mockResolvedValue({ id: 42, userId: 1 } as any);
    prismaMock.calendarCache.deleteMany.mockResolvedValue({ count: 3 });
    await expect(deleteCacheHandler({ ctx, input: { credentialId: 42 } }))
      .resolves.toEqual({ success: true });
    expect(prismaMock.calendarCache.deleteMany)
      .toHaveBeenCalledWith({ where: { credentialId: 42 } });
  });
});
```

## Improvements

:yellow_circle: [cross-file-impact] `delegationCredentialId` prop passed as `string | undefined` where the component declares `string | null` in `packages/platform/atoms/selected-calendars/wrappers/SelectedCalendarsSettingsWebWrapper.tsx`:76 (confidence: 87)
`CredentialActionsDropdown` declares `delegationCredentialId?: string | null`. The same PR correctly coerces with `connectedCalendar.delegationCredentialId || null` when passing this id to `CalendarSwitch` (around the `CalendarSwitch` call), but the two `CredentialActionsDropdown` call sites in this file (the first in the connected-calendar branch and the second in the fallback/no-primary branch) both pass `delegationCredentialId={connectedCalendar.delegationCredentialId}` without the `|| null` coercion. Runtime behavior is fine because `!delegationCredentialId` inside the component treats `null` and `undefined` identically, but under strict `noImplicitAny`/`exactOptionalPropertyTypes` TypeScript configurations the two call sites can surface as type errors or conflict with tRPC-inferred optionality. Normalize for consistency with the `CalendarSwitch` site in this same diff.
```suggestion
                <CredentialActionsDropdown
                  credentialId={connectedCalendar.credentialId}
                  integrationType={connectedCalendar.integration.type}
                  cacheUpdatedAt={connectedCalendar.cacheUpdatedAt}
                  onSuccess={onChanged}
                  delegationCredentialId={connectedCalendar.delegationCredentialId || null}
                  disableConnectionModification={disableConnectionModification}
                />
```

:yellow_circle: [testing] `getCacheStatusByCredentialIds` has no coverage for the three branches of the Prisma `groupBy` aggregation in `packages/features/calendar-cache/calendar-cache.repository.ts`:172 (confidence: 90)
The method wraps a `prisma.calendarCache.groupBy({ by: ["credentialId"], _max: { updatedAt: true } })`. Three behaviors need regression coverage because they drive the UI "Last updated" label for every connected Google Calendar: (1) empty `credentialIds` array → should return `[]` without issuing a query that scans the whole table; (2) credentialIds with no matching cache rows → the credential should be reported as "no cache" (today: absent from the map → `null` via `cacheStatusMap.get(...) || null`); (3) a credential with multiple cache rows → `_max.updatedAt` returns the latest. Any schema rename of `updatedAt` or change in Prisma's `groupBy` return shape will silently break the UI without a test.
```suggestion
describe("CalendarCacheRepository.getCacheStatusByCredentialIds", () => {
  it("returns empty when called with no credentialIds", async () => {
    const result = await repo.getCacheStatusByCredentialIds([]);
    expect(result).toEqual([]);
  });
  it("omits credentials with no cache rows", async () => {
    prismaMock.calendarCache.groupBy.mockResolvedValue([]);
    expect(await repo.getCacheStatusByCredentialIds([7])).toEqual([]);
  });
  it("surfaces the latest updatedAt via _max", async () => {
    const t = new Date("2025-07-20T10:00:00Z");
    prismaMock.calendarCache.groupBy.mockResolvedValue([
      { credentialId: 5, _max: { updatedAt: t } } as any,
    ]);
    const result = await repo.getCacheStatusByCredentialIds([5]);
    expect(result).toEqual([{ credentialId: 5, updatedAt: t }]);
  });
});
```

:yellow_circle: [testing] `CredentialActionsDropdown` has four distinct render states driven by `hasCache` × `canDisconnect` and none are tested in `packages/features/apps/components/CredentialActionsDropdown.tsx`:119 (confidence: 88)
The component computes `hasCache = isGoogleCalendar && cacheUpdatedAt` and `canDisconnect = !delegationCredentialId && !disableConnectionModification`, then renders `null` when both are false. Because this component replaced `DisconnectIntegration` at two call sites in the wrapper, a logic inversion (e.g. flipping the `!canDisconnect && !hasCache` guard, or using `||` instead of `&&`) would either silently hide disconnect for every user or leave the dropdown rendering for delegation credentials it is meant to suppress. React Testing Library tests for each of the four (hasCache, canDisconnect) combinations — especially the `(false, false) → renders nothing` path — give cheap regression protection for a silent-UI-removal bug class.
```suggestion
describe("CredentialActionsDropdown", () => {
  const base = { credentialId: 1, integrationType: "google_calendar", onSuccess: jest.fn() };
  it("renders nothing when delegation credential has no cache", () => {
    const { container } = render(
      <CredentialActionsDropdown {...base} delegationCredentialId="del-1" cacheUpdatedAt={null} />
    );
    expect(container).toBeEmptyDOMElement();
  });
  it("shows only the delete-cache action when disconnect is disabled", async () => {
    render(<CredentialActionsDropdown {...base} delegationCredentialId="del-1" cacheUpdatedAt={new Date()} />);
    await userEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/delete cached data/i)).toBeVisible();
    expect(screen.queryByText(/remove app/i)).toBeNull();
  });
  it("shows only disconnect for non-Google integrations", async () => {
    render(<CredentialActionsDropdown {...base} integrationType="office365_calendar" cacheUpdatedAt={new Date()} />);
    await userEvent.click(screen.getByRole("button"));
    expect(screen.queryByText(/delete cached data/i)).toBeNull();
    expect(screen.getByText(/remove app/i)).toBeVisible();
  });
  it("shows both actions for a user-owned Google credential with cache", async () => {
    render(<CredentialActionsDropdown {...base} cacheUpdatedAt={new Date()} />);
    await userEvent.click(screen.getByRole("button"));
    expect(screen.getByText(/delete cached data/i)).toBeVisible();
    expect(screen.getByText(/remove app/i)).toBeVisible();
  });
});
```

## Risk Metadata
Risk Score: 55/100 (MEDIUM) | Blast Radius: 17 files, 379 additions / 31 deletions across credential auth handler, Prisma schema + migration, and two replaced UI render paths (`SelectedCalendarsSettingsWebWrapper` — connected-calendar branch and fallback branch) | Sensitive Paths: `packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts` (new auth-sensitive TRPC mutation), `packages/prisma/migrations/20250715160635_add_calendar_cache_updated_at/migration.sql` (NOT NULL column add with `DEFAULT NOW()` — safe on Postgres 11+, metadata-only), `packages/prisma/schema.prisma` (model shape), `packages/app-store/googlecalendar/lib/CalendarService.ts` (credential-scoped side effect on every fetch).
AI-Authored Likelihood: HIGH (branch name `devin/calendar-cache-tooltip-1752595047` indicates Devin-generated; confirmed patterns: narrative comments on non-obvious single lines — "Update SelectedCalendar.updatedAt for all calendars under this credential" (the comment is correct about intent but the code does not implement it), "Provide an initial value for legacy rows and future raw inserts"; copy-paste asymmetry where `disconnectMutation` has `onSettled` invalidation and `deleteCacheMutation` does not; extended `Pick<SelectedCalendar, ...>` with unused-in-this-PR fields `updatedAt` and `googleChannelId` added to `UserWithCalendars` but no call site consumes them in the diff).

(5 additional findings below confidence threshold: plain `new Error` vs `TRPCError` in deleteCache.handler [75], direct `prisma` access vs `CalendarCacheRepository.deleteManyByCredential` in deleteCache.handler [65], stray blank line in SelectedCalendarsSettingsWebWrapper [70], BSD-only `sed -i ''` in `scripts/test-gcal-webhooks.sh` [80], and a redundant test suggestion for `SelectedCalendarRepository.updateManyByCredentialId` that conflicts with the critical finding to remove its single caller.)
