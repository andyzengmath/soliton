## Summary
22 files changed, ~380 lines added, ~220 lines deleted. 13 findings (10 critical, 3 improvements).
`destinationCalendar` initialized as `null` in `handleNewBooking` is silently discarded by a subsequent `?.push(...)`, so the Collective-events bug this PR is meant to fix (CAL-1267 / #7754) is not actually fixed for the common case where neither the eventType nor the organizer has a personal destination calendar. Multiple additional critical defects (runtime TypeError in `EventManager`, interface mismatch in Lark/Office365 Calendar services, IDOR in the new DB credential fallback, inverted billing gate in the org-create handler) compound the risk.

## Critical

:red_circle: [correctness] Unguarded property access on possibly-undefined destructured value — crash when destinationCalendar is empty or null in packages/core/EventManager.ts:277 (confidence: 99)
`const [mainHostDestinationCalendar] = evt.destinationCalendar ?? [];` is immediately followed by `mainHostDestinationCalendar.integration` without optional chaining. When `destinationCalendar` is `null`, `undefined`, or `[]` the destructured variable is `undefined` and `.integration` throws a `TypeError` at runtime. The original code used `evt.destinationCalendar?.integration` which was safe. Multiple call sites in this PR now produce `destinationCalendar: []` (bookingReminder.ts, webhook.ts, paypal-webhook.ts, confirm.handler.ts, editLocation.handler.ts, requestReschedule.handler.ts, deleteCredential.handler.ts), so this path will be exercised in production.
```suggestion
const [mainHostDestinationCalendar] = evt.destinationCalendar ?? [];
if (evt.location === MeetLocationType && mainHostDestinationCalendar?.integration !== "google_calendar") {
  evt["location"] = "integrations:daily";
}
```

:red_circle: [correctness] destinationCalendar initialized as null silently discards all team calendars — the core Collective-events fix does not work in packages/features/bookings/lib/handleNewBooking.ts:861 (confidence: 97)
`evt` is built with `destinationCalendar: eventType.destinationCalendar ? [eventType.destinationCalendar] : organizerUser.destinationCalendar ? [organizerUser.destinationCalendar] : null`. The subsequent line `evt.destinationCalendar?.push(...teamDestinationCalendars)` uses optional chaining, which short-circuits on `null` and silently discards every collected team-member calendar. This is exactly the scenario (eventType/organizer without a personal calendar, team members with calendars) that this PR was meant to fix. The failure is silent — no error, no calendar invite sent to additional hosts. Every other PR site uses `[]` as the empty fallback; `null` here is inconsistent and the direct cause of the regression.
```suggestion
const organizerDestinationCalendars: DestinationCalendar[] = eventType.destinationCalendar
  ? [eventType.destinationCalendar]
  : organizerUser.destinationCalendar
  ? [organizerUser.destinationCalendar]
  : [];

const evt: CalendarEvent = {
  // ...
  destinationCalendar: organizerDestinationCalendars,
  // ...
};

if (isTeamEventType && eventType.schedulingType === "COLLECTIVE") {
  evt.destinationCalendar!.push(...teamDestinationCalendars);
}
```

:red_circle: [cross-file-impact] LarkCalendarService.createEvent missing required credentialId parameter — Calendar interface mismatch in packages/app-store/larkcalendar/lib/CalendarService.ts:125 (confidence: 97)
The `Calendar` interface in `packages/types/Calendar.d.ts` was updated to require `createEvent(event: CalendarEvent, credentialId: number)`. `LarkCalendarService.createEvent` was not updated — the class still declares `implements Calendar` but no longer satisfies the contract. TypeScript will raise a structural-type error; at runtime the extra argument is silently ignored so per-credential calendar routing is not performed and collective events with Lark destinations cannot be correctly partitioned.
```suggestion
async createEvent(event: CalendarEvent, credentialId: number): Promise<NewCalendarEventType> {
  // ... existing body; consider using credentialId to pick the matching destinationCalendar entry
}
```

:red_circle: [cross-file-impact] Office365CalendarService.createEvent missing required credentialId parameter — Calendar interface mismatch in packages/app-store/office365calendar/lib/CalendarService.ts:70 (confidence: 97)
Same interface violation as Lark. Additionally, unlike `GoogleCalendarService`, Office365 performs no `credentialId`-based calendar selection — it always unconditionally uses `destinationCalendar[0]`, so multi-host Office365 collective bookings still route every host's event to only the first calendar after this PR. The stated fix is incomplete for Microsoft 365 users.
```suggestion
async createEvent(event: CalendarEvent, credentialId: number): Promise<NewCalendarEventType> {
  const matched = event.destinationCalendar?.find((cal) => cal.credentialId === credentialId);
  const [mainHostDestinationCalendar] = event.destinationCalendar ?? [];
  const targetCalendar = matched ?? mainHostDestinationCalendar;
  const eventsUrl = targetCalendar?.externalId
    ? `/me/calendars/${targetCalendar.externalId}/events`
    : "/me/calendar/events";
  // ...
}
```

:red_circle: [correctness] Dead find in updateEvent and deleteEvent — else-branch always yields undefined in packages/app-store/googlecalendar/lib/CalendarService.ts:253 (confidence: 95)
The fallback expressions `externalCalendarId ? externalCalendarId : event.destinationCalendar?.find(cal => cal.externalId === externalCalendarId)?.externalId` (updateEvent, line 253) and the same pattern in `deleteEvent` (line 312 of the diff) are logically broken: the else branch only runs when `externalCalendarId` is falsy, and the `find` predicate compares `cal.externalId` against that same falsy value, so it can never match any real calendar entry. `selectedCalendar` / `calendarId` is therefore always `undefined` in the else branch. `calendar.events.update({ calendarId: undefined })` and `calendar.events.delete({ calendarId: undefined })` will either default to `primary` (wrong calendar) or 400 — silently updating / deleting events in the wrong place.
```suggestion
const selectedCalendar = externalCalendarId
  ? externalCalendarId
  : event.destinationCalendar?.[0]?.externalId ?? "primary";
```

:red_circle: [correctness] updateEvent called with undefined credential after DB fallback fails — will crash in callee in packages/core/EventManager.ts:454 (confidence: 92)
In `_updateAllCalendarEvents`, if the DB-fallback `prisma.credential.findUnique` cannot reconstruct the credential (for example, `credentialFromDB.app?.slug` is falsy), the outer `let credential` variable remains its initial `undefined`. The code then pushes `updateEvent(credential, event, bookingRefUid, calenderExternalId)` unconditionally. `createAllCalendarEvents` correctly guards the same path with `if (credential) { ... }`; the asymmetry means update paths crash where create paths degrade gracefully.
```suggestion
if (credentialFromDB && credentialFromDB.app?.slug) {
  credential = { /* ... */ };
}
if (credential) {
  result.push(updateEvent(credential, event, bookingRefUid, calenderExternalId));
}
```

:red_circle: [correctness] Billing-gate inversion — slug persistence is backwards, enabling tenant-name squatting on SaaS and breaking tenant-URL routing on self-host in packages/trpc/server/routers/viewer/organizations/create.handler.ts:151 (confidence: 93)
The original code wrote `slug` immediately when billing was DISABLED (`...(!IS_TEAM_BILLING_ENABLED && { slug })`) and `requestedSlug` when billing was ENABLED (`...(IS_TEAM_BILLING_ENABLED && { requestedSlug: slug })`). The new code is `...(IS_TEAM_BILLING_ENABLED ? { slug } : {})` and `...(IS_TEAM_BILLING_ENABLED ? { requestedSlug: slug } : {})`. This inverts the `slug` condition and additionally writes BOTH `slug` and `requestedSlug` on billing-enabled deployments. Consequences: (1) on billing-enabled SaaS (cal.com), any authenticated user hard-commits the requested slug to `Organization.slug` (unique column) BEFORE payment completes — attackers can squat "admin", brand names, or competitor names by starting and abandoning the billing flow; (2) on self-host (billing disabled), the slug is never persisted at all, breaking tenant-URL routing. This change is entirely unrelated to the PR's stated goal and looks like an accidental refactor.
```suggestion
organization: {
  create: {
    name,
    ...(!IS_TEAM_BILLING_ENABLED && { slug }),
    metadata: {
      ...(IS_TEAM_BILLING_ENABLED && { requestedSlug: slug }),
      isOrganization: true,
      isOrganizationVerified: false,
      isOrganizationConfigured,
      // ...
    },
  },
}
```
[References: OWASP A04:2021 Insecure Design, OWASP A01:2021, CWE-840]

:red_circle: [security] Unscoped credential lookup in createAllCalendarEvents enables cross-user OAuth credential use (IDOR) in packages/core/EventManager.ts:322 (confidence: 85)
When `destination.credentialId` is absent from `this.calendarCredentials` (the organizer-scoped credential set), the code now falls back to `prisma.credential.findUnique({ where: { id: destination.credentialId } })` with no ownership check. The returned credential — including `key` (OAuth access/refresh tokens) — is passed straight into `createEvent`, which issues calendar-API writes authenticated as that credential's owner. Because `CalendarEvent.destinationCalendar[]` is assembled upstream from `eventType.destinationCalendar`, `organizerUser.destinationCalendar`, and each team user's `user.destinationCalendar`, any path that can influence those rows (including booking a collective event with a crafted or stale destinationCalendarId) can cause EventManager to issue OAuth-authenticated calendar writes using another user's token. The same structural pattern also appears in `_updateAllCalendarEvents` (lines 423–463) and `handleCancelBooking` (lines 620–640); both were flagged at confidence 80 and suppressed — they should be fixed together.
```suggestion
const allowedUserIds = new Set(
  [event.organizer?.id, ...(event.team?.members?.map((m) => m.id) ?? [])].filter(Boolean)
);
const allowedTeamIds = new Set([event.team?.id].filter(Boolean));
const credentialFromDB = await prisma.credential.findFirst({
  include: { app: { select: { slug: true } } },
  where: {
    id: destination.credentialId,
    OR: [
      { userId: { in: Array.from(allowedUserIds) } },
      ...(allowedTeamIds.size ? [{ teamId: { in: Array.from(allowedTeamIds) } }] : []),
    ],
  },
});
if (!credentialFromDB) continue;
// ...
```
[References: OWASP A01:2021 Broken Access Control, CWE-639, CWE-863]

:red_circle: [testing] createAllCalendarEvents multi-host iteration loop has zero unit test coverage in packages/core/EventManager.ts:334 (confidence: 98)
The entire refactored loop — array iteration, in-memory credential lookup, DB-fallback reconstruction, and empty-array fallthrough — has no automated tests. The PR author explicitly acknowledges no tests were added ("I haven't added tests that prove my fix is effective or that my feature works"). A regression in any branch silently drops calendar invites for one or more hosts with no error signal. The runtime crash (finding #1), the null-push silent loss (finding #2), the undefined-credential crash (finding #6), and the IDOR (finding #8) are all located inside this untested region and would each have been caught by a straightforward unit test.
```suggestion
// Add packages/core/EventManager.test.ts (Jest, mocked prisma and createEvent) covering:
// 1. multi-destination dispatch: N destinations → N createEvent calls, one per credential
// 2. DB-fallback path: credentialId not in this.calendarCredentials → prisma.credential.findUnique called, credential used
// 3. DB-fallback returns null / missing app.slug → skip gracefully (no crash)
// 4. empty destinationCalendar array → fall through to the "all calendar credentials" branch
// 5. TypeError guard: destinationCalendar = [] and location = MeetLocationType → evt.location downgraded to integrations:daily, no crash
```

:red_circle: [testing] Collective booking destinationCalendar assembly for multiple hosts is completely untested in packages/features/bookings/lib/handleNewBooking.ts:832 (confidence: 96)
The core feature fix — iterating team members, collecting each user's `destinationCalendar` into `teamDestinationCalendars`, and pushing them onto `evt.destinationCalendar` — has no test. Critical missing scenarios: (1) 2-host collective booking where both hosts have a destinationCalendar → evt.destinationCalendar should contain both entries; (2) hosts with no destinationCalendar → silently skipped, no null/undefined in the array; (3) ROUND_ROBIN booking → team calendars must NOT be collected. The null-initialization bug (finding #2) would be caught immediately by scenario (1) when the organizer has no personal calendar.
```suggestion
// Add an integration test in packages/features/bookings/lib/handleNewBooking.test.ts:
// - mock Prisma so two hosts are returned with different destinationCalendar values,
//   eventType.schedulingType = COLLECTIVE, eventType.destinationCalendar = null, organizer.destinationCalendar = null
// - call handler(req) and assert that the resulting evt.destinationCalendar array length === 2
//   and contains both hosts' calendar entries
// - add a parallel test for ROUND_ROBIN that asserts team calendars are NOT collected
```

## Improvements

:yellow_circle: [correctness] credentialId fallback dropped from BookingReference — credentialId will be undefined for some calendar results in packages/core/EventManager.ts:298 (confidence: 91)
The old code populated `BookingReference.credentialId` as `result.credentialId ?? evt.destinationCalendar?.credentialId`. The new code is `result.credentialId ?? undefined`, which is equivalent to `result.credentialId` — the fallback to the destination calendar's credentialId has been silently dropped. For calendar results that do not set their own `credentialId` (some integrations only surface externalId), the stored `BookingReference` row will carry `credentialId: undefined`. Future update/delete flows that look up the correct credential via `reference.credentialId` will miss, causing silent no-ops or fall into the IDOR DB-fallback path (finding #8).
```suggestion
credentialId: result.credentialId ?? evt.destinationCalendar?.[0]?.credentialId,
```

:yellow_circle: [consistency] Redundant length check in destructuring fallback — verbose and inconsistent with rest of PR in packages/app-store/googlecalendar/lib/CalendarService.ts:54 (confidence: 90)
GoogleCalendarService uses the verbose guard `calEventRaw?.destinationCalendar && calEventRaw?.destinationCalendar.length > 0 ? calEventRaw.destinationCalendar : []` before destructuring. Every other file modified in this PR (LarkCalendarService, Office365CalendarService, BaseCalendarService, EventManager, BrokenIntegrationEmail) uses the simpler `event.destinationCalendar ?? []`. The `.length > 0` branch is dead code because destructuring an empty array already yields `undefined` for the first element.
```suggestion
const [mainHostDestinationCalendar] = calEventRaw?.destinationCalendar ?? [];
```

:yellow_circle: [correctness] loadUsers drops organization.slug from user select — downstream reads of user.organization?.slug will silently return undefined in packages/features/bookings/lib/handleNewBooking.ts:759 (confidence: 88)
The pre-refactor `prisma.user.findMany` select in the dynamic-booking branch included `organization: { select: { slug: true } }`. The refactored `loadUsers` omits this field. Downstream code that reads `user.organization?.slug` — for organization-scoped routing, profile URLs, and email generation — will now silently receive `undefined` for dynamic bookings, which can break organization-branded email links and redirect logic. This is a silent regression unrelated to the PR's stated goal.
```suggestion
const users = await prisma.user.findMany({
  where: { username: { in: dynamicUserList } },
  select: {
    ...userSelect.select,
    credentials: true,
    metadata: true,
    organization: {
      select: { slug: true },
    },
  },
});
```

## Risk Metadata
Risk Score: 67/100 (HIGH) | Blast Radius: CalendarEvent type and Calendar interface touched — type is imported across ~40 files; interface change is not reflected in Lark/Office365 implementors. Payment webhooks (stripe, paypal), deleteCredential handler, and cron booking reminder all reshape destinationCalendar. | Sensitive Paths: packages/features/ee/payments/api/webhook.ts, packages/features/ee/payments/api/paypal-webhook.ts, packages/trpc/server/routers/loggedInViewer/deleteCredential.handler.ts
AI-Authored Likelihood: LOW

(7 additional findings below confidence threshold: unscoped credential lookup in updateAllCalendarEvents [security, 80], unscoped credential lookup in handleCancelBooking [security, 80], requestReschedule drops user.destinationCalendar fallback [cross-file-impact, 80], loadUsers returns undefined [correctness, 82], SchedulingType enum type-safety loss [consistency, 80], HTTP-500 error masking in loadUsers [security, 65], createAllCalendarEvents fallback branch missing externalId [correctness, 72])
