## Summary
17 files changed, 399 lines added, 31 lines deleted. 9 findings (3 critical, 4 improvements, 2 nitpicks).
Adds Google Calendar cache status display + `deleteCache` tRPC mutation and a new `CredentialActionsDropdown`; core feature works, but contains a hot-path write amplification bug, an incomplete authorization check, and a likely no-op Prisma `updateMany({data: {}})`.

## Critical

:red_circle: [correctness/performance] Write amplification on every availability fetch in `GoogleCalendarService.getAvailability` in packages/app-store/googlecalendar/lib/CalendarService.ts:1022 (confidence: 95)
`SelectedCalendarRepository.updateManyByCredentialId(this.credential.id, {})` is called unconditionally at the end of `getAvailability`, which runs on every booking page load / availability check. This issues an `UPDATE selectedCalendar SET ... WHERE credentialId = ?` against potentially many rows on every read-path call, causing:
1. A write per availability lookup on a hot path (N writes per page / user / slot pick),
2. Row-level lock contention with the Google watch/renew flow that also writes to `SelectedCalendar`,
3. Unnecessary replication / WAL traffic.

Also, because `data` is `{}`, Prisma may either (a) issue an UPDATE that only bumps `@updatedAt`, or (b) short-circuit because there are no scalar fields being set — behavior depends on the Prisma version and is not documented as stable. Either way this is a poor way to express "touch these rows".

```suggestion
    // Only touch SelectedCalendar.updatedAt when the cache was actually refreshed,
    // and do it explicitly rather than via an empty-data updateMany.
    if (refreshedCache) {
      await SelectedCalendarRepository.updateManyByCredentialId(this.credential.id, {
        updatedAt: new Date(),
      });
    }
```
References: Prisma issue #4072 (updateMany with empty data), Prisma `@updatedAt` semantics.

:red_circle: [security] deleteCache authorization check misses team/delegated credentials in packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:15 (confidence: 85)
The handler authorizes with `where: { id: credentialId, userId: user.id }`. In the Cal.com model, a `Credential` can be owned by a team (`teamId`) or be a delegation credential; in both cases `userId` may be `null` or not equal to the acting user. Consequences:
1. Team admins cannot delete the cache of team-owned calendar credentials they legitimately manage (feature gap / false negative).
2. More importantly, the UI renders `CredentialActionsDropdown` for *all* connected calendars including team ones and exposes the "Delete cached data" action, so users will hit a generic `Error: "Credential not found or access denied"` with no differentiation — a UX bug that can also mask real permission failures.

Replace the ad-hoc check with the same ownership/membership helper used by `credentials.delete` (which correctly handles team + delegation credentials), or explicitly hide the "Delete cached data" action for credentials the user cannot modify.

```suggestion
import { canAccessCredential } from "@calcom/lib/server/credentials/canAccessCredential";
// ...
const credential = await prisma.credential.findUnique({ where: { id: credentialId } });
if (!credential || !(await canAccessCredential({ user, credential }))) {
  throw new TRPCError({ code: "FORBIDDEN", message: "Cannot modify this credential" });
}
```
References: OWASP A01 (Broken Access Control).

:red_circle: [correctness] deleteCacheHandler throws a plain `Error` instead of TRPCError in packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:20 (confidence: 90)
`throw new Error("Credential not found or access denied")` inside a tRPC mutation propagates as an `INTERNAL_SERVER_ERROR` with a generic message, not a `FORBIDDEN`/`NOT_FOUND`. The client-side `onError` shows `t("error_deleting_cache")` unconditionally, so a genuine 500 (DB outage) and an authorization failure look identical — bad for both UX and server observability. Other handlers in this router use `TRPCError`.

```suggestion
import { TRPCError } from "@trpc/server";
// ...
if (!credential) {
  throw new TRPCError({ code: "NOT_FOUND", message: "Credential not found or access denied" });
}
```

## Improvements

:yellow_circle: [testing] No tests for the new `deleteCacheHandler` or `getCacheStatusByCredentialIds` in packages/trpc/server/routers/viewer/calendars/deleteCache.handler.ts:1 (confidence: 90)
This PR adds a mutation that deletes data and performs an authorization check, plus a new repository method backing the UI status display. Neither has unit tests. Given the authorization concerns above, regression coverage is essential. At minimum add:
1. `deleteCacheHandler` denies a user deleting another user's credential's cache.
2. `deleteCacheHandler` succeeds for the owner and removes rows.
3. `getCacheStatusByCredentialIds` returns the max `updatedAt` per credentialId.

```suggestion
// packages/trpc/server/routers/viewer/calendars/__tests__/deleteCache.handler.test.ts
// Use the existing prisma-mock harness and the pattern from connectedCalendars.handler.test.ts.
```

:yellow_circle: [correctness] Cache invalidation missing after successful `deleteCache` in packages/features/apps/components/CredentialActionsDropdown.tsx:92 (confidence: 80)
`deleteCacheMutation` only calls `onSuccess?.()` but never invalidates `utils.viewer.calendars.connectedCalendars`, unlike the sibling `disconnectMutation` which invalidates both `connectedCalendars` and `apps.integrations` in `onSettled`. As a result, after a successful cache delete the "Last updated" label will still show the old timestamp until an unrelated refetch fires.

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

:yellow_circle: [cross-file-impact] `UserWithCalendars` type widened but not all producers updated in packages/lib/getConnectedDestinationCalendars.ts:21 (confidence: 75)
`allSelectedCalendars` / `userLevelSelectedCalendars` now additionally `Pick` `updatedAt` and `googleChannelId`. `UserRepository.findUserWithCalendars` was updated to select these, but there are other call sites in the repo that construct `UserWithCalendars`-compatible objects (search for `allSelectedCalendars:` in tests and mocks). Any producer not selecting the new fields will now be a TypeScript error or, worse, runtime-`undefined` that sneaks past `Pick`. Verify every producer selects both fields.

:yellow_circle: [correctness] Dev script uses BSD-only `sed -i ''` in scripts/test-gcal-webhooks.sh:60 (confidence: 95)
`sed -i '' -E ...` is BSD syntax (macOS). On Linux/CI runners with GNU sed this will try to edit a file literally named `-E` and fail. If the script is macOS-only, say so in a comment or guard; otherwise use a sed invocation that works on both (e.g., `sed -i.bak -E ... && rm "$ENV_FILE.bak"`, or use `perl -i -pe`).

## Nitpicks

:white_circle: [consistency] `ts-node` → `npx tsx` change is unrelated to the PR goal in apps/web/package.json:9 (confidence: 90)
The `dev:cron` script change has nothing to do with calendar cache status/actions. Unrelated diffs bloat the review and make reverts / bisects harder. Ship separately.

:white_circle: [consistency] Migration comment contradicts the actual DDL in packages/prisma/migrations/20250715160635_add_calendar_cache_updated_at/migration.sql:3 (confidence: 85)
The auto-generated warning header says the column was added "without a default value", but the DDL below explicitly adds `DEFAULT NOW()`. The warning was left over from Prisma's initial generation and is misleading for anyone reading the migration later. Either delete the stale `/* Warnings */` block or rewrite it to match what the migration actually does.

## Risk Metadata
Risk Score: 62/100 (HIGH) | Blast Radius: touches tRPC router, Google Calendar hot-path availability, Prisma schema + migration, and 2 UI wrappers | Sensitive Paths: migration (`packages/prisma/migrations/*`), authorization handler (`deleteCache.handler.ts`), credential dropdown UI
AI-Authored Likelihood: HIGH (head branch `devin/calendar-cache-tooltip-1752595047`, boilerplate patterns, overly-literal comment on schema.prisma, empty-object updateMany)
