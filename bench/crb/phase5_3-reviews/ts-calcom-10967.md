## Summary
22 files changed, 368 lines added, 216 lines deleted. 14 findings (10 critical, 4 improvements).
Refactoring `CalendarEvent.destinationCalendar` from a single object to an array introduces multiple null-deref crashes, tautological calendar lookups that silently target the wrong calendar, missing interface updates in two calendar implementations, several cross-tenant credential-bleed paths, and a regression in the very collective-host scenario this PR aims to fix.

## Critical

:red_circle: [correctness] Null dereference on empty `destinationCalendar` array — missing optional chain on `.integration` in `packages/core/EventManager.ts`:277 (confidence: 97)
After the single→array refactor, the code destructures `const [mainHostDestinationCalendar] = evt.destinationCalendar ?? [];` and immediately accesses `mainHostDestinationCalendar.integration` without optional chaining. When `destinationCalendar` is `null`, `undefined`, or `[]` (all valid states under the new type, and explicitly produced by `handleNewBooking.ts`, `confirm.handler.ts`, and others), `mainHostDestinationCalendar` is `undefined` and `.integration` throws `TypeError: Cannot read properties of undefined (reading 'integration')`, crashing the booking-create flow. The pre-PR code used `evt.destinationCalendar?.integration` which short-circuited safely.
```suggestion
if (evt.location === MeetLocationType && mainHostDestinationCalendar?.integration !== "google_calendar") {
```

:red_circle: [correctness] Tautological `.find()` makes `selectedCalendar` always `undefined` in `updateEvent` in `packages/app-store/googlecalendar/lib/CalendarService.ts`:117 (confidence: 96)
The new fallback is `event.destinationCalendar?.find((cal) => cal.externalId === externalCalendarId)?.externalId`, but this branch is only reached when `externalCalendarId` is itself falsy (the `else` of the ternary). The `find` predicate then compares each calendar's `externalId` against the same falsy value — a comparison that can never match a real calendar. `selectedCalendar` is always `undefined` in the fallback, so Google receives `calendarId: undefined` for every update lacking an explicit `externalCalendarId`, silently updating the wrong calendar or 404-ing. The pre-PR fallback was `event.destinationCalendar?.externalId`, which correctly returned the stored calendar.
```suggestion
const selectedCalendar = externalCalendarId
  ? externalCalendarId
  : event.destinationCalendar?.find((cal) => cal.credentialId === credentialId)?.externalId
    ?? event.destinationCalendar?.[0]?.externalId;
```

:red_circle: [correctness] Tautological `.find()` makes `calendarId` always `undefined` in `deleteEvent` in `packages/app-store/googlecalendar/lib/CalendarService.ts`:128 (confidence: 96)
Same anti-pattern as `updateEvent`: `event.destinationCalendar?.find((cal) => cal.externalId === externalCalendarId)?.externalId` is only reached when `externalCalendarId` is falsy, so the predicate is structurally unsatisfiable and `calendarId` is always `undefined`. `defaultCalendarId = "primary"` is declared but never used after the refactor (dead variable confirming that `"primary"` was the intended fallback). Calendar deletes silently target `undefined` instead of the booking's actual calendar, leaving orphaned events.
```suggestion
const calendarId = externalCalendarId
  ? externalCalendarId
  : event.destinationCalendar?.find((cal) => cal.credentialId === credentialId)?.externalId
    ?? defaultCalendarId;
```

:red_circle: [correctness] `LarkCalendarService.createEvent` does not accept `credentialId` parameter required by `Calendar` interface in `packages/app-store/larkcalendar/lib/CalendarService.ts`:137 (confidence: 90)
`packages/types/Calendar.d.ts` updates the `Calendar` interface to require `createEvent(event: CalendarEvent, credentialId: number)`. `packages/core/CalendarManager.ts` was updated to call `calendar.createEvent(calEvent, credential.id)`. `LarkCalendarService.createEvent` was updated for the destinationCalendar array change but its signature remains `async createEvent(event: CalendarEvent)` — one parameter. This is a TypeScript compile error (`Class 'LarkCalendarService' incorrectly implements interface 'Calendar'`) and silently drops `credentialId` at runtime, breaking any per-host credential routing for Lark.
```suggestion
async createEvent(event: CalendarEvent, credentialId: number): Promise<NewCalendarEventType> {
```

:red_circle: [correctness] `Office365CalendarService.createEvent` does not accept `credentialId` parameter required by `Calendar` interface in `packages/app-store/office365calendar/lib/CalendarService.ts`:183 (confidence: 90)
Same interface violation as `LarkCalendarService`. `Office365CalendarService.createEvent` retains its single-argument signature `async createEvent(event: CalendarEvent)` while the `Calendar` interface and `CalendarManager` caller now pass two arguments. TypeScript will refuse to build under strict mode, and the per-host `credentialId` is silently dropped.
```suggestion
async createEvent(event: CalendarEvent, credentialId: number): Promise<NewCalendarEventType> {
```

:red_circle: [correctness] `updateEvent` called with `undefined` credential when DB-fallback fails in `packages/core/EventManager.ts`:454 (confidence: 92)
In the new `for (const reference of calendarReference)` loop in `updateAllCalendarEvents`, the `if (credentialFromDB && credentialFromDB.app?.slug)` block only assigns the local `credential` on its happy path. The very next line, `result.push(updateEvent(credential, event, bookingRefUid, calenderExternalId))`, is outside that conditional with no guard. If `credentialFromDB` is null or `app.slug` is falsy, `credential` is `undefined` and `updateEvent(undefined, ...)` is called, which dereferences `credential` inside `getCalendar` and throws asynchronously. The parallel `createAllCalendarEvents` correctly wraps the push in `if (credential) { ... }`.
```suggestion
if (credential) {
  result.push(updateEvent(credential, event, bookingRefUid, calenderExternalId));
} else {
  log.error("updateAllCalendarEvents: credential not found for reference", reference.credentialId);
}
```

:red_circle: [correctness] Collective team destination calendars silently dropped when `evt.destinationCalendar` is null in `packages/features/bookings/lib/handleNewBooking.ts`:876 (confidence: 91)
`evt.destinationCalendar` is initialised to `null` (third branch of the ternary) when neither `eventType.destinationCalendar` nor `organizerUser.destinationCalendar` is set. The follow-up `evt.destinationCalendar?.push(...teamDestinationCalendars)` then no-ops via the optional chain, and every collective host's calendar is silently discarded. This is precisely the scenario this PR is supposed to fix: a collective event whose organizer has no personal destination calendar but whose team members do. The fix only works when the organizer already has a destination calendar configured.
```suggestion
destinationCalendar: eventType.destinationCalendar
  ? [eventType.destinationCalendar]
  : organizerUser.destinationCalendar
  ? [organizerUser.destinationCalendar]
  : [],
// later, after teamDestinationCalendars is built:
if (isTeamEventType && eventType.schedulingType === "COLLECTIVE") {
  evt.destinationCalendar = [...(evt.destinationCalendar ?? []), ...teamDestinationCalendars];
}
```

:red_circle: [security] Cross-tenant credential lookup in `createAllCalendarEvents` (IDOR / credential bleed) in `packages/core/EventManager.ts`:327 (confidence: 92)
When `this.calendarCredentials` (scoped to the booking organizer) does not contain `destination.credentialId`, the new code calls `prisma.credential.findUnique({ where: { id: destination.credentialId } })` with no ownership check. With multi-host destinationCalendars, any participating host can persist a `credentialId` pointing to another tenant's OAuth token; the server then loads that credential and uses it to create an event in the victim's external calendar (CWE-639, OWASP A01). This crosses tenant boundaries via a confused-deputy server-side action.
```suggestion
const allowedCredentialIds = new Set(
  hosts.flatMap((h) => h.user.credentials.map((c) => c.id))
);
if (!allowedCredentialIds.has(destination.credentialId)) {
  log.warn("Rejecting destinationCalendar credentialId outside host allowlist", destination.credentialId);
  continue;
}
const credentialFromDB = await prisma.credential.findUnique({
  where: { id: destination.credentialId },
  include: { app: { select: { slug: true } } },
});
```
References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/639.html

:red_circle: [security] Cross-tenant credential lookup in `updateAllCalendarEvents` (IDOR) in `packages/core/EventManager.ts`:441 (confidence: 90)
The same anti-pattern in `updateAllCalendarEvents`: `prisma.credential.findUnique({ where: { id: reference.credentialId } })` with no `userId` constraint. `BookingReference.credentialId` can now originate from any participating host once destinationCalendar is multi-valued; on subsequent reschedule/update, the server will load and use that victim's OAuth token to update the wrong calendar. Defense in depth requires scoping the query to host-owned credentials.
```suggestion
const allowedCredentialIds = new Set(
  bookingHosts.flatMap((h) => h.credentials?.map((c) => c.id) ?? [])
);
if (!allowedCredentialIds.has(reference.credentialId)) continue;
const credentialFromDB = await prisma.credential.findUnique({
  where: { id: reference.credentialId },
  include: { app: { select: { slug: true } } },
});
```
References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/, https://cwe.mitre.org/data/definitions/441.html

:red_circle: [security] `handleCancelBooking` uses credential fetched by ID without ownership check in `packages/features/bookings/lib/handleCancelBooking.ts`:628 (confidence: 88)
When cancelling, the new loop falls back to `prisma.credential.findUnique({ where: { id: credentialId } })` with no `userId` filter when the credentialId is not in `bookingToDelete.user.credentials`. The resulting credential is passed straight to `getCalendar` and `calendar.deleteEvent`. A user entitled to cancel a booking can cause the server to load and use another tenant's OAuth credential to delete events in the victim's calendar — a destructive cross-tenant authorization bypass (CWE-639). The pre-refactor code only iterated `bookingToDelete.user.credentials`, so this fallback is a new exposure.
```suggestion
const allowedUserIds = [
  bookingToDelete.userId,
  ...(bookingToDelete.eventType?.hosts?.map((h) => h.userId) ?? []),
].filter((id): id is number => id != null);
const foundCalendarCredential = await prisma.credential.findFirst({
  where: { id: credentialId, userId: { in: allowedUserIds } },
  include: { app: { select: { slug: true } } },
});
if (!foundCalendarCredential) {
  logger.warn("Refusing to use credential outside booking host scope", credentialId);
  continue;
}
```
References: https://owasp.org/Top10/A01_2021-Broken_Access_Control/

## Improvements

:yellow_circle: [silent-failure] `updateAllCalendarEvents` catch returns `[]` indistinguishable from "no calendars" in `packages/core/EventManager.ts`:514 (confidence: 88)
`calendarReference` is declared `undefined` outside the try block and only assigned inside it. The catch block now does `calendarReference?.map(...) ?? []`. If the exception is thrown before assignment (e.g., the `prisma.booking.findFirst` call), the result is `[]` — exactly what the success path returns when there are no references. Callers cannot distinguish "no calendars to update" from "all calendar updates failed with an exception". The previous code returned a synthetic `{ success: false }` entry. Combined with the removal of `console.error(message)`, the exception is now logged nowhere.
```suggestion
} catch (error) {
  log.error("updateAllCalendarEvents failed", error);
  return [{ appName: "none", type: "calendar", success: false, uid: "", originalEvent: event, credentialId: 0 }];
}
```

:yellow_circle: [hallucination] `handleCancelBooking` DB credential fetch missing `app.slug` include and `appName` synthesis in `packages/features/bookings/lib/handleCancelBooking.ts`:628 (confidence: 90)
The new fallback `prisma.credential.findUnique({ where: { id: credentialId } })` omits `include: { app: { select: { slug: true } } }`, but the result is passed to `getCalendar`, which expects `CredentialPayload` / `CredentialWithAppName` containing an `appName` field. Compare to `EventManager.ts` and `CalendarManager.ts` in this same diff, which both correctly include the relation and synthesise `appName: credentialFromDB?.app.slug ?? ""`. Without it, calendar resolution will silently target the wrong (or no) integration.
```suggestion
const foundCalendarCredential = await prisma.credential.findFirst({
  where: { id: credentialId },
  include: { app: { select: { slug: true } } },
});
calendarCredential = foundCalendarCredential
  ? { ...foundCalendarCredential, appName: foundCalendarCredential.app?.slug ?? "" }
  : null;
```

:yellow_circle: [correctness] Collective cancel fan-out duplicates delete calls in `packages/features/bookings/lib/handleCancelBooking.ts`:619 (confidence: 87)
When `bookingCalendarReference` contains multiple references (one per collective host) and a reference has no `credentialId` (legacy bookings), the else branch loops over all `_calendar` credentials and pushes `calendar?.deleteEvent` for each. This inner loop runs once per outer reference — N×M delete API calls for what should be M. Redundant deletes against external calendar providers can rate-limit or trigger spurious failures.
```suggestion
let ranLegacyFanOut = false;
for (const reference of bookingCalendarReference) {
  if (reference.credentialId) {
    /* per-credential path */
  } else if (!ranLegacyFanOut) {
    ranLegacyFanOut = true;
    /* legacy fan-out */
  }
}
```

:yellow_circle: [security] Google organizer email exposes index-0 host's private calendar address in `packages/app-store/googlecalendar/lib/CalendarService.ts`:53 (confidence: 87)
In `createEvent` (and `updateEvent`), the Google event organizer email is now derived from `mainHostDestinationCalendar.externalId`, which is `event.destinationCalendar[0].externalId`. With this PR, `destinationCalendar` for COLLECTIVE events contains every host in iteration order — the first one is arbitrary. Because Google calendar `externalId` is typically the host's primary calendar email (often a private corporate address), the chosen "organizer" email leaks an arbitrary host's private address to all attendees of every collective booking, regardless of who was supposed to be the public-facing organizer (CWE-200).
```suggestion
const ownDestinationCalendar = calEventRaw.destinationCalendar?.find(
  (cal) => cal.credentialId === credentialId
);
const organizerEmail = ownDestinationCalendar?.externalId ?? calEventRaw.organizer.email;
```
References: https://cwe.mitre.org/data/definitions/200.html

## Risk Metadata
Risk Score: 82/100 (HIGH) | Blast Radius: 22 files, booking/cancel/payment-webhook/calendar core paths | Sensitive Paths: payment webhooks (`paypal-webhook.ts`, `webhook.ts`), credential handling (`deleteCredential.handler.ts`), OAuth credential lookups (multiple)
AI-Authored Likelihood: MEDIUM (multiple repeated logic mistakes — tautological find pattern duplicated in two places, optional-chaining missing in one place where it was correct pre-PR, fabricated `firstName`/`lastName` Person fields)
