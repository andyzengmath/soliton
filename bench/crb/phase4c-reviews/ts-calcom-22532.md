## Summary
17 files changed, 379 lines added, 30 lines deleted. 9 findings (2 critical, 7 improvements).
Adds calendar cache status + delete UI, a new `deleteCache` tRPC mutation, and a `CalendarCache.updatedAt` column; introduces a runtime no-op and a feature-flag bypass that will break cache freshness signalling.

## Critical
:red_circle: [correctness] `updateManyByCredentialId(credentialId, {})` is a no-op that won't bump `SelectedCalendar.updatedAt` in `packages/app-store/googlecalendar/lib/CalendarService.ts:1022` (confidence: 92)
The new call after `fetchAvailabilityAndSetCache` passes an **empty** `data` object. Prisma's `updateMany` with no field writes produces `UPDATE … SET … WHERE …` with no assignments — Prisma either throws (`PrismaClientValidationError: Argument data.updatedAt is missing`) on some versions or emits no SQL on others, and crucially the `@updatedAt` Prisma attribute only fires when at least one other column is being written. The explicit goal of this line is to refresh `SelectedCalendar.updatedAt` so the tooltip can show "Last updated"; in its current form it will never fire. Every "Last updated" timestamp in the new dropdown will therefore be stuck at whatever value was last written by the existing subscription/watch path — silent staleness that will look fine in QA until the cache repopulates.
```suggestion
    // Update SelectedCalendar.updatedAt for all calendars under this credential
    await SelectedCalendarRepository.updateManyByCredentialId(this.credential.id, {
      updatedAt: new Date(),
    });
```

:red_circle: [cross-file-impact] `connectedCalendars.handler.ts` bypasses the `CalendarCacheRepository` factory and will hit Prisma even when calendar-cache is disabled in `packages/trpc/server/routers/viewer/calendars/connectedCalendars.handler.ts:27` (confidence: 88)
`new CalendarCacheRepository()` is instantiated directly. The rest of the codebase resolves this class through a factory that returns `CalendarCacheRepositoryMock` (see `calendar-cache.repository.mock.ts`, whose new `getCacheStatusByCredentialIds` already returns `[]` and logs "Skipping … due to calendar-cache being disabled"). Calling the concrete repo unconditionally means: (a) on self-hosted instances with the cache feature off, every call to `connectedCalendars` now issues an extra `groupBy` against `CalendarCache`, and (b) in environments where the table or column is missing (e.g. before the new migration lands, or on skinny dev DBs), the mutation fails with a Prisma schema error and the whole Connected Calendars settings page goes blank. The mock path was added to the interface specifically to avoid this.
```suggestion
import { CalendarCacheRepository } from "@calcom/features/calendar-cache/calendar-cache.repository.factory";

// ...inside the handler…
const cacheRepository = CalendarCacheRepository.getRepository();
const cacheStatuses = await cacheRepository.getCacheStatusByCredentialIds(credentialIds);
```

## Improvements
:yellow_circle: [consistency] `deleteCacheMutation.onSuccess` does not invalidate `viewer.calendars.connectedCalendars`, so the tooltip keeps showing the old timestamp after deletion in `packages/features/apps/components/CredentialActionsDropdown.tsx:40` (confidence: 90)
Compare to `disconnectMutation` a few lines below, which correctly calls `utils.viewer.calendars.connectedCalendars.invalidate()` inside `onSettled`. After a user clicks "Delete cached data" and the server deletes the rows, the component still receives the stale `cacheUpdatedAt` prop from the parent query, so the dropdown continues to show "Last updated …" with the old value until a full page refresh. Given the whole point of the dropdown is to surface cache state, this is visibly broken.
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

:yellow_circle: [security] `deleteCacheHandler` authorization check rejects team-owned credentials without distinguishing "not yours" from "not found" in `packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:15` (confidence: 78)
`prisma.credential.findFirst({ where: { id: credentialId, userId: user.id } })` only matches user-level credentials. Cal.com also has team-level credentials where `userId` is null and authorization flows through team membership (see how `credentials.delete` and other calendar handlers resolve access). As written, no team admin can ever delete team-level calendar cache — and more importantly, users will see "Credential not found or access denied" for credentials that actually exist and that they can see listed on the same page, which is confusing. Align with the pattern already used by `credentials.delete` (fetch credential with `OR: [{ userId }, { team: { members: { some: { userId, accepted: true, role: { in: ["ADMIN", "OWNER"] } } } } }]`).
```suggestion
  const credential = await prisma.credential.findFirst({
    where: {
      id: credentialId,
      OR: [
        { userId: user.id },
        {
          team: {
            members: {
              some: {
                userId: user.id,
                accepted: true,
                role: { in: ["ADMIN", "OWNER"] },
              },
            },
          },
        },
      ],
    },
  });
```

:yellow_circle: [correctness] `sed -i ''` on line 66 of the new `test-gcal-webhooks.sh` is BSD/macOS-only and breaks on Linux in `scripts/test-gcal-webhooks.sh:66` (confidence: 95)
GNU `sed` (every Linux distro and every CI runner cal.com uses) rejects `sed -i '' …` with `sed: can't read : No such file or directory` — BSD/macOS requires the empty string as the in-place-backup argument, GNU wants no extra argument. Either drop the `''` and keep the script Linux-only, or detect the OS. Since the script is a dev-loop helper for Google Calendar webhooks on engineers' laptops, make it portable.
```suggestion
if grep -q '^GOOGLE_WEBHOOK_URL=' "$ENV_FILE"; then
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' -E "s|^GOOGLE_WEBHOOK_URL=.*|GOOGLE_WEBHOOK_URL=$TUNNEL_URL|" "$ENV_FILE"
  else
    sed -i -E "s|^GOOGLE_WEBHOOK_URL=.*|GOOGLE_WEBHOOK_URL=$TUNNEL_URL|" "$ENV_FILE"
  fi
else
  echo "GOOGLE_WEBHOOK_URL=$TUNNEL_URL" >> "$ENV_FILE"
fi
```

:yellow_circle: [correctness] Migration backfills every existing `CalendarCache` row with the same `NOW()`, producing a misleading "Last updated: <migration time>" for hours/days until cache repopulates in `packages/prisma/migrations/20250715160635_add_calendar_cache_updated_at/migration.sql:9` (confidence: 72)
`DEFAULT NOW()` on the `ADD COLUMN` is a safe default for the column constraint, but it stamps every legacy row with the identical migration timestamp. On large tenants where cache entries can be days or weeks old, the UI will show "Last updated: <deploy time>" uniformly, which actively misinforms operators who are using the tooltip to decide whether to bust a stale cache. A safer approach is to backfill with `expiresAt - cacheTtl` where possible, or to leave the column nullable and render "Unknown" when `updatedAt` predates the deploy. The generated comment "This is not possible if the table is not empty" is also stale (the `DEFAULT NOW()` clause was added precisely to make this possible) and should be removed to avoid confusing future readers.

:yellow_circle: [correctness] Empty wrapper `<div className="flex w-32 justify-end">` is rendered even when `CredentialActionsDropdown` returns `null`, leaving 128px of dead space in the header in `packages/platform/atoms/selected-calendars/wrappers/SelectedCalendarsSettingsWebWrapper.tsx:70` (confidence: 85)
The previous code guarded the whole `<div>` with `!connectedCalendar.delegationCredentialId && !disableConnectionModification`. This PR moves the guard *inside* `CredentialActionsDropdown` (which returns `null` when `!canDisconnect && !hasCache`) but leaves the fixed-width wrapper `<div>` in place at both call sites. For delegation-credential / read-only-mode rows with no cache, the layout reserves empty space where the dropdown used to live, shifting alignment and leaving a visible gap. Either move the wrapper div into the component or keep a guarded ternary in the wrapper.
```suggestion
              actions={
                <CredentialActionsDropdown
                  credentialId={connectedCalendar.credentialId}
                  integrationType={connectedCalendar.integration.type}
                  cacheUpdatedAt={connectedCalendar.cacheUpdatedAt}
                  onSuccess={onChanged}
                  delegationCredentialId={connectedCalendar.delegationCredentialId}
                  disableConnectionModification={disableConnectionModification}
                />
              }
```
(and move the `<div className="flex w-32 justify-end">` inside `CredentialActionsDropdown`'s returned JSX so it disappears together with the dropdown.)

:yellow_circle: [consistency] `hasCache = isGoogleCalendar && cacheUpdatedAt` hard-codes cache UX to Google Calendar, even though the underlying `CalendarCacheRepository` is provider-agnostic and `deleteCacheHandler` deletes by `credentialId` regardless of type in `packages/features/apps/components/CredentialActionsDropdown.tsx:56` (confidence: 68)
The repository and mutation are written generically — any future integration (Microsoft Graph, etc.) that populates `CalendarCache` will silently have its entries deletable from the backend but invisible in the UI. If the intent is "only Google currently writes to CalendarCache", encode that in the cache-writing side (or a constant list of cache-enabled types) rather than in the component boolean so the next integration doesn't have to chase this flag.
```suggestion
  const hasCache = CACHE_ENABLED_INTEGRATIONS.includes(integrationType) && Boolean(cacheUpdatedAt);
```

:yellow_circle: [consistency] Dropping `satisfies Prisma.SelectedCalendarFindManyArgs` removes a type check on a method used across many callers in `packages/lib/server/repository/selectedCalendar.ts:260` (confidence: 55)
The `satisfies` assertion was the only thing keeping the inline args shape typed against Prisma's generated input. Removing it doesn't change runtime behavior but strips type safety from a widely-called helper, which seems unrelated to the PR's theme (the change is purely cosmetic — same keys, same order). Recommend reverting this hunk to keep the PR surgical and preserve the type guard.
```suggestion
  static async findMany({ where, select, orderBy }: FindManyArgs) {
    const args = { where, select, orderBy } satisfies Prisma.SelectedCalendarFindManyArgs;
    return await prisma.selectedCalendar.findMany(args);
  }
```

## Risk Metadata
Risk Score: 58/100 (MEDIUM) | Blast Radius: 17 files; touches DB schema (CalendarCache), a shared repository, a tRPC router, and the main Connected-Calendars settings UI — every authenticated user with a calendar integration hits the modified `connectedCalendars` handler | Sensitive Paths: `packages/prisma/migrations/**`, tRPC auth handlers
AI-Authored Likelihood: MEDIUM — branch name `devin/calendar-cache-tooltip-1752595047` and the unrelated `ts-node → npx tsx` / `satisfies`-removal drive-bys are consistent with agent output, but the invention-disclosure-style PR body and test script polish suggest human editing on top.
