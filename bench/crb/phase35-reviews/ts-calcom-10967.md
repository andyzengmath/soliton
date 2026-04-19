## Summary
22 files changed, 363 lines added, 218 lines deleted. 10 findings (4 critical, 6 improvements).
Widens `CalendarEvent.destinationCalendar` from single object to array so collective events can fan-out to every team host's calendar, but the fan-out introduces several latent crashes, one inverted-conditional regression in an unrelated org-create path, and broken selector logic in Google Calendar update/delete.

## Critical
:red_circle: [correctness] Inverted `slug` conditional in organization create unrelated to PR goal in packages/trpc/server/routers/viewer/organizations/create.handler.ts:151 (confidence: 98)
The refactor from `&&`-spread to ternary inverts the `slug` branch. Previously `slug` was attached when team billing was **disabled** (`!IS_TEAM_BILLING_ENABLED && { slug }`); the new code attaches it when billing is **enabled** (`IS_TEAM_BILLING_ENABLED ? { slug } : {}`). The sibling `requestedSlug` branch kept the correct polarity, so both `slug` and `requestedSlug` now populate under the same flag — an org created with billing enabled will write a permanent slug instead of a pending `metadata.requestedSlug`, breaking the Stripe-gated slug reservation flow. This change is unrelated to the collective-attendee fix and should not be in this PR at all.
```suggestion
            ...(!IS_TEAM_BILLING_ENABLED ? { slug } : {}),
            metadata: {
              ...(IS_TEAM_BILLING_ENABLED ? { requestedSlug: slug } : {}),
```

:red_circle: [correctness] Null-deref when `evt.destinationCalendar` is empty/null in packages/core/EventManager.ts:277 (confidence: 95)
```js
const [mainHostDestinationCalendar] = evt.destinationCalendar ?? [];
if (evt.location === MeetLocationType && mainHostDestinationCalendar.integration !== "google_calendar") {
```
`mainHostDestinationCalendar` is `undefined` whenever `destinationCalendar` is null, undefined, or an empty array (all three are possible given the new type `DestinationCalendar[] | null` and the caller in `handleNewBooking.ts:1063` that now sets `null` when no calendar is configured). Accessing `.integration` on `undefined` throws `TypeError`, which will crash every Meet-location booking whose organizer has no destination calendar — a common case. The old code used `evt.destinationCalendar?.integration`, which returned `undefined` and correctly fell through to `"integrations:daily"`.
```suggestion
    const [mainHostDestinationCalendar] = evt.destinationCalendar ?? [];
    if (evt.location === MeetLocationType && mainHostDestinationCalendar?.integration !== "google_calendar") {
```

:red_circle: [correctness] `selectedCalendar` lookup matches against a known-falsy value in packages/app-store/googlecalendar/lib/CalendarService.ts:256 (confidence: 97)
```js
const selectedCalendar = externalCalendarId
  ? externalCalendarId
  : event.destinationCalendar?.find((cal) => cal.externalId === externalCalendarId)?.externalId;
```
The else-branch runs precisely when `externalCalendarId` is falsy (`undefined`/`null`/`""`), yet `.find` then searches for a calendar whose `externalId === externalCalendarId` — i.e. equal to that same falsy value. This will only "succeed" by chance if a destination calendar also has a falsy externalId, making the result effectively always `undefined`. The old fallback was `event.destinationCalendar?.externalId`, which always resolved to the organizer's calendar. The identical mistake is duplicated at `deleteEvent` (line 315). Both `updateEvent` and `deleteEvent` now silently target no explicit calendar (the Google API then falls back to `primary` in insert, but for update/delete this is a real loss of routing). Correct lookup key is likely `credentialId` (passed down from `EventManager`) or `externalId` of the first destination:
```suggestion
      const selectedCalendar =
        externalCalendarId ||
        event.destinationCalendar?.find((cal) => cal.credentialId === this.credential.id)?.externalId;
```

:red_circle: [correctness] Raw `Credential` row assigned to `CredentialWithAppName`-typed variable in packages/features/bookings/lib/handleCancelBooking.ts:440 (confidence: 92)
```js
const foundCalendarCredential = await prisma.credential.findUnique({
  where: { id: credentialId },
});
if (foundCalendarCredential) {
  calendarCredential = foundCalendarCredential;
}
```
Unlike the equivalent DB-fallback path added in `EventManager.ts:327-349`, this query omits the `include: { app: { select: { slug: true } } }` block and assigns the raw `Credential` to a variable whose type downstream (consumed by `getCalendar(calendarCredential)`) expects an `appName` field. Downstream `getCalendar` keys off `appName`, so the returned calendar adapter will be `null`/`undefined` for every DB-fallback cancellation, silently skipping calendar deletion for bookings whose credential is no longer on the user record (the exact case this fallback exists to handle).
```suggestion
          const foundCalendarCredential = await prisma.credential.findUnique({
            include: { app: { select: { slug: true } } },
            where: { id: credentialId },
          });
          if (foundCalendarCredential && foundCalendarCredential.app?.slug) {
            calendarCredential = {
              appName: foundCalendarCredential.app.slug,
              id: foundCalendarCredential.id,
              type: foundCalendarCredential.type,
              key: foundCalendarCredential.key,
              userId: foundCalendarCredential.userId,
              teamId: foundCalendarCredential.teamId,
              invalid: foundCalendarCredential.invalid,
              appId: foundCalendarCredential.appId,
            };
          }
```

## Improvements
:yellow_circle: [correctness] Recurring-event deletion now runs once per calendar reference causing N× duplicate API calls in packages/features/bookings/lib/handleCancelBooking.ts:418 (confidence: 85)
The outer `for (const reference of bookingCalendarReference)` wraps the pre-existing `if (recurringEvent && allRemainingBookings)` branch, which already iterates every `_calendar` credential across every `updatedBookings` entry. With collective events now producing multiple `_calendar` references per booking, this nested structure multiplies the delete workload: 3 host calendars × N recurring instances × M user credentials. Beyond wasted API quota, Google returns 410 on repeat deletes, so the second-and-beyond passes may log noisy errors and mask legitimate failures. The recurring-all-remaining branch should be lifted out of the per-reference loop.

:yellow_circle: [cross-file-impact] `loadUsers` dynamic-user branch drops `organization` selector in packages/features/bookings/lib/handleNewBooking.ts:757 (confidence: 80)
The rewritten `loadUsers` no longer includes `organization: { select: { slug: true } }` in the dynamic-user `findMany`. Downstream code — e.g. `getOrgSlugFromUsername` and any of the organization-scoped slots lookups — reads `user.organization?.slug` on the returned rows. Dynamic (username-in-URL) bookings for org users will now see `undefined` where an org slug used to be, silently routing the booking into the non-org flow. Restore the `organization` select or confirm no consumer reads it on this path.

:yellow_circle: [correctness] `updateAllCalendarEvents` can push an undefined credential into `updateEvent` in packages/core/EventManager.ts:496 (confidence: 85)
Inside the new `for (const reference of calendarReference)` loop, if the primary `this.calendarCredentials.filter(...)[0]` lookup misses AND the DB fallback misses (e.g., credential deleted), `credential` remains `undefined`, yet `result.push(updateEvent(credential, event, bookingRefUid, calenderExternalId))` is called unconditionally outside the `if (credentialFromDB && credentialFromDB.app?.slug)` guard. `updateEvent` in `CalendarManager.ts` calls `getCalendar(credential)` which will throw on `undefined.type`. The `createAllCalendarEvents` path above has the symmetric bug fixed with an `if (credential)` guard — mirror that here.

:yellow_circle: [correctness] `loadUsers` eventTypeId branch returns `eventType.users` with mismatched `isFixed` shape in packages/features/bookings/lib/handleNewBooking.ts:799 (confidence: 75)
```js
const users = hosts.map(({ user, isFixed }) => ({ ...user, isFixed }));
return users.length ? users : eventType.users;
```
When `hosts` is empty, this returns the raw `eventType.users` (no `isFixed` field); when non-empty, rows carry `isFixed`. Downstream `IsFixedAwareUser[]` treats `isFixed: undefined` as `false`, so round-robin logic that depends on this flag silently misroutes bookings. The prior code matched this shape via `eventType.hosts?.length ? ... : eventType.users || []` and did not pretend the fallback carried `isFixed`. Either annotate the fallback or reshape downstream handling so the distinction is intentional.

:yellow_circle: [hallucination] Hardcoded empty `firstName` / `lastName` fields added to team-member payload in packages/features/bookings/lib/handleNewBooking.ts:838 (confidence: 78)
```js
return {
  email: user.email ?? "",
  name: user.name || "",
  firstName: "",
  lastName: "",
  timeZone: user.timeZone,
```
`Person`/team-member types in `packages/types/Calendar.d.ts` don't declare `firstName`/`lastName` at all. Either these fields are silently dropped by the downstream consumer (dead code that reads as AI-authored filler and should be removed) or there is a real downstream reader that will receive empty strings instead of real values — the commit message gives no hint which. Recommend removing unless a specific email-template consumer was shown to need them, in which case populate from `user.name` split.

:yellow_circle: [testing] No unit coverage for the multi-destination fan-out in packages/core/EventManager.ts (confidence: 90)
The core behavioral change — `createAllCalendarEvents` iterating over each team host's `DestinationCalendar` and the credential-fallback DB lookup — has zero direct test coverage. The only test edit in the diff is a fixture tweak in `webhook.e2e.ts` changing `destinationCalendar: null` → `destinationCalendar: []`. Given the blast radius (22 files, 3 calendar integrations, payments/cancel/reschedule paths), at minimum add a unit test asserting (a) a COLLECTIVE event with 3 hosts writes 3 separate calendar events with correct `externalId`/`credentialId` on each `BookingReference`, and (b) the DB-fallback branch is exercised when the organizer's credentials don't contain the host's credentialId.

## Risk Metadata
Risk Score: 76/100 (HIGH) | Blast Radius: cross-package type change to `CalendarEvent.destinationCalendar` affects 22 files across core, app-store (3 integrations), bookings features, payments webhooks, trpc routers; `Calendar.createEvent` interface signature change breaks ABI for all third-party calendar adapters | Sensitive Paths: packages/features/ee/payments/api/webhook.ts, packages/features/ee/payments/api/paypal-webhook.ts, packages/trpc/server/routers/loggedInViewer/deleteCredential.handler.ts
AI-Authored Likelihood: MEDIUM — loadUsers rewrite shows characteristic AI patterns (defensive `Array.isArray` check on a Prisma-typed field, generic "is not properly defined or empty" error strings, redundant try/catch wrapping typed throws, hardcoded `firstName: ""`/`lastName: ""` with no consumer).
